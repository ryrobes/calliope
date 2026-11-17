//! Binding the fast-path calls in a reader's replacement queries.
//!
//! The rewriter builds a replacement query as SQL that calls one of rvbbit's
//! fast-path functions (`SELECT ... FROM rvbbit.vector_float_agg(...)`), and
//! for some rules most of that SQL is the user's own text. A reader has no
//! USAGE on schema rvbbit, so it can't resolve `rvbbit.<name>`, and SQL that
//! carries user text is never parsed as rvbbit's owner. So before analysis each
//! fast-path call is swapped for a stand-in the reader can parse, made only of
//! pg_catalog functions and holding the same arguments and result columns:
//!
//! - `FROM rvbbit.f(args) [AS a(cols)]` stands in as
//!   `FROM pg_catalog.jsonb_to_recordset(pg_catalog.jsonb_build_array(args)) AS a(<f's columns>)`
//! - `rvbbit.f(args)` as a select-list item or under a cast stands in as
//!   `CASE WHEN pg_catalog.jsonb_build_array(args) IS NULL THEN NULL::<f's type> END`
//!
//! Once the query is analyzed, each stand-in is replaced by a call to the real
//! function by OID with the arguments coerced to its parameter types: the tree
//! a parse with USAGE would have produced. Calling a function by OID needs only
//! EXECUTE on it, and every function listed here checks
//! `catalog_owner::require_readable` on its tables when it runs.
//!
//! Stand-ins are told apart by their parse location, which no user text has.
//! Any other call into schema rvbbit leaves the query unbindable, and the
//! rewrite is skipped.

use std::ffi::{c_void, CStr, CString};

use pgrx::pg_sys;

/// The fast-path functions a reader's replacement query may call.
const READER_FAST_PATHS: &[&str] = &[
    "agg_groupby_avg",
    "agg_groupby_count",
    "agg_groupby_sum",
    "any_count_int_text",
    "count_distinct_int",
    "count_text_contains",
    "top_avg_len_by_int_col",
    "top_count_1col",
    "top_count_distinct_1col",
    "top_count_distinct_int_text",
    "top_count_filtered",
    "top_count_int_minute_text",
    "top_count_int_text",
    "top_phrase_min_url_for_url_contains",
    "top_phrase_url_title_rollup",
    "top_rollup_1int_distinct",
    "top_rollup_2int",
    "top_rows_text_contains_ordered_json",
    "top_searchphrase_ordered",
    "top_text_transform_avg_len",
    "vector_filtered_distinct_int_keys",
    "vector_float_agg",
    "vector_group_member_filtered_rows",
    "vector_int_key_text_filter_rows_1i64_2i32_3f64",
    "vector_sum_having_keys",
];

/// Parse location of the first stand-in; the next ones count down from it.
const FIRST_MARK: i32 = -1_000_000;

struct StandIn {
    funcid: pg_sys::Oid,
    /// The call's own alias, put back on its range table entry.
    alias: *mut pg_sys::Alias,
    /// Its column definition list came from the query (a function returning
    /// bare `record`), so the bound entry keeps it.
    keeps_coldefs: bool,
    bound: bool,
}

/// The stand-ins put into one raw statement, to be bound after analysis.
pub(crate) struct StandIns {
    calls: Vec<StandIn>,
    failed: bool,
}

/// Replaces the fast-path calls in `stmt` (a raw statement) with stand-ins.
/// None when it calls into schema rvbbit any other way.
pub(crate) unsafe fn stand_in(stmt: *mut pg_sys::Node) -> Option<StandIns> {
    let mut cx = StandIns {
        calls: Vec::new(),
        failed: false,
    };
    stand_in_walker(stmt, (&mut cx as *mut StandIns).cast());
    (!cx.failed).then_some(cx)
}

/// Replaces each stand-in in `query` (analyzed from the statement `stand_in`
/// rewrote) with its real call. False if any could not be bound.
pub(crate) unsafe fn bind(query: *mut pg_sys::Query, mut stand_ins: StandIns) -> bool {
    if stand_ins.calls.is_empty() {
        return true;
    }
    bind_walker(query.cast(), (&mut stand_ins as *mut StandIns).cast());
    !stand_ins.failed && stand_ins.calls.iter().all(|call| call.bound)
}

unsafe extern "C-unwind" fn stand_in_walker(node: *mut pg_sys::Node, context: *mut c_void) -> bool {
    if node.is_null() {
        return false;
    }
    let cx = &mut *(context as *mut StandIns);
    match (*node).type_ {
        pg_sys::NodeTag::T_RangeFunction => stand_in_from(node.cast(), cx),
        pg_sys::NodeTag::T_ResTarget => {
            let target = node as *mut pg_sys::ResTarget;
            // Name the column now, from the call: a stand-in would name it "case".
            if (*target).name.is_null() {
                if let Some(name) = cast_rvbbit_call_name((*target).val) {
                    (*target).name = pstrdup(&name);
                }
            }
            stand_in_scalar(&mut (*target).val, cx)
        }
        pg_sys::NodeTag::T_TypeCast => stand_in_scalar(&mut (*(node as *mut pg_sys::TypeCast)).arg, cx),
        // Any call into rvbbit still here is one no stand-in covers.
        pg_sys::NodeTag::T_FuncCall if rvbbit_call_name(node.cast()).is_some() => cx.failed = true,
        _ => {}
    }
    if cx.failed {
        return true;
    }
    pg_sys::raw_expression_tree_walker_impl(node, Some(stand_in_walker), context)
}

unsafe fn stand_in_from(rf: *mut pg_sys::RangeFunction, cx: &mut StandIns) {
    if (*rf).is_rowsfrom || list_len((*rf).functions) != 1 {
        return;
    }
    let pair = list_ptr((*rf).functions, 0) as *mut pg_sys::List;
    let call = list_ptr(pair, 0) as *mut pg_sys::FuncCall;
    if call.is_null() || (*call).type_ != pg_sys::NodeTag::T_FuncCall {
        return;
    }
    let Some(name) = rvbbit_call_name(call) else {
        return;
    };
    let Some(funcid) = fast_path_function(call, &name) else {
        cx.failed = true;
        return;
    };
    let alias = (*rf).alias;
    let alias_names = alias_colnames(alias);
    let mut rettype = pg_sys::InvalidOid;
    let mut tupdesc: pg_sys::TupleDesc = std::ptr::null_mut();
    let columns: Vec<(String, pg_sys::Oid, i32)> =
        match pg_sys::get_func_result_type(funcid, &mut rettype, &mut tupdesc) {
            pg_sys::TypeFuncClass::TYPEFUNC_COMPOSITE if !tupdesc.is_null() => {
                let mut out = Vec::new();
                for i in 0..(*tupdesc).natts {
                    let attr = pg_sys::TupleDescAttr(tupdesc, i);
                    if (*attr).attisdropped {
                        continue;
                    }
                    let own = CStr::from_ptr((*attr).attname.data.as_ptr())
                        .to_string_lossy()
                        .into_owned();
                    let name = alias_names.get(out.len()).cloned().unwrap_or(own);
                    out.push((name, (*attr).atttypid, (*attr).atttypmod));
                }
                out
            }
            // A scalar function's one column is named as PostgreSQL names it
            // (chooseScalarFunctionAlias): its one OUT parameter, else the
            // alias, else the function, and an alias column name over all.
            pg_sys::TypeFuncClass::TYPEFUNC_SCALAR => {
                let out_name = pg_sys::get_func_result_name(funcid);
                let own = if !out_name.is_null() {
                    CStr::from_ptr(out_name).to_string_lossy().into_owned()
                } else if !alias.is_null() {
                    CStr::from_ptr((*alias).aliasname).to_string_lossy().into_owned()
                } else {
                    name.clone()
                };
                vec![(alias_names.first().cloned().unwrap_or(own), rettype, -1)]
            }
            pg_sys::TypeFuncClass::TYPEFUNC_RECORD if !(*rf).coldeflist.is_null() => Vec::new(),
            _ => {
                cx.failed = true;
                return;
            }
        };
    let keeps_coldefs = columns.is_empty();
    if !keeps_coldefs && alias_names.len() > columns.len() {
        cx.failed = true;
        return;
    }
    let build_args = make_call("jsonb_build_array", (*call).args, -1);
    let to_recordset = make_call(
        "jsonb_to_recordset",
        pg_sys::lappend(std::ptr::null_mut(), build_args.cast()),
        mark(cx.calls.len()),
    );
    (*(*pair).elements).ptr_value = to_recordset.cast();
    let alias_name = if alias.is_null() {
        pstrdup(&name)
    } else {
        (*alias).aliasname
    };
    (*rf).alias = pg_sys::makeAlias(alias_name, std::ptr::null_mut());
    if !keeps_coldefs {
        let mut coldefs = std::ptr::null_mut();
        for (col, typid, typmod) in &columns {
            let def = pg_sys::makeColumnDef(pstrdup(col), *typid, *typmod, pg_sys::InvalidOid);
            coldefs = pg_sys::lappend(coldefs, def.cast());
        }
        (*rf).coldeflist = coldefs;
    }
    cx.calls.push(StandIn {
        funcid,
        alias,
        keeps_coldefs,
        bound: false,
    });
}

unsafe fn stand_in_scalar(slot: *mut *mut pg_sys::Node, cx: &mut StandIns) {
    let node = *slot;
    if node.is_null() || (*node).type_ != pg_sys::NodeTag::T_FuncCall {
        return;
    }
    let call = node as *mut pg_sys::FuncCall;
    let Some(name) = rvbbit_call_name(call) else {
        return;
    };
    let Some(funcid) = fast_path_function(call, &name) else {
        cx.failed = true;
        return;
    };
    let rettype = pg_sys::get_func_rettype(funcid);
    if pg_sys::get_func_retset(funcid) || is_pseudo_type(rettype) {
        cx.failed = true;
        return;
    }
    let Some(case) = case_stand_in((*call).args, rettype, mark(cx.calls.len())) else {
        cx.failed = true;
        return;
    };
    *slot = case;
    cx.calls.push(StandIn {
        funcid,
        alias: std::ptr::null_mut(),
        keeps_coldefs: false,
        bound: false,
    });
}

/// `CASE WHEN pg_catalog.jsonb_build_array(<args>) IS NULL THEN NULL::<rettype> END`
/// as a raw expression.
unsafe fn case_stand_in(args: *mut pg_sys::List, rettype: pg_sys::Oid, location: i32) -> Option<*mut pg_sys::Node> {
    let sql = c"SELECT CASE WHEN pg_catalog.jsonb_build_array() IS NULL THEN NULL::pg_catalog.int4 END";
    let parsed = pg_sys::raw_parser(sql.as_ptr(), pg_sys::RawParseMode::RAW_PARSE_DEFAULT);
    let raw = list_ptr(parsed, 0) as *mut pg_sys::RawStmt;
    let select = (*raw).stmt as *mut pg_sys::SelectStmt;
    let target = list_ptr((*select).targetList, 0) as *mut pg_sys::ResTarget;
    let case = (*target).val as *mut pg_sys::CaseExpr;
    let when = list_ptr((*case).args, 0) as *mut pg_sys::CaseWhen;
    let test = (*when).expr as *mut pg_sys::NullTest;
    let build_args = (*test).arg as *mut pg_sys::FuncCall;
    let cast = (*when).result as *mut pg_sys::TypeCast;
    if [
        (*case).xpr.type_ == pg_sys::NodeTag::T_CaseExpr,
        (*test).xpr.type_ == pg_sys::NodeTag::T_NullTest,
        (*build_args).type_ == pg_sys::NodeTag::T_FuncCall,
        (*cast).type_ == pg_sys::NodeTag::T_TypeCast,
    ]
    .contains(&false)
    {
        return None;
    }
    (*build_args).args = args;
    (*cast).typeName = pg_sys::makeTypeNameFromOid(rettype, -1);
    (*case).location = location;
    Some(case.cast())
}

unsafe extern "C-unwind" fn bind_walker(node: *mut pg_sys::Node, context: *mut c_void) -> bool {
    if node.is_null() {
        return false;
    }
    let cx = &mut *(context as *mut StandIns);
    match (*node).type_ {
        pg_sys::NodeTag::T_Query => {
            return pg_sys::query_tree_walker_impl(
                node.cast(),
                Some(bind_walker),
                context,
                pg_sys::QTW_EXAMINE_RTES_BEFORE as i32,
            );
        }
        pg_sys::NodeTag::T_RangeTblEntry => {
            bind_from(node.cast(), cx);
            return cx.failed;
        }
        // The slots a scalar stand-in can sit in once analyzed: a target
        // entry, any expression list (function and operator arguments, casts
        // included), and the binary and I/O coercions.
        pg_sys::NodeTag::T_TargetEntry => {
            bind_scalar(&mut (*(node as *mut pg_sys::TargetEntry)).expr as *mut _ as *mut *mut pg_sys::Node, cx)
        }
        pg_sys::NodeTag::T_List => {
            let list = node as *mut pg_sys::List;
            for i in 0..(*list).length {
                let cell = (*list).elements.add(i as usize);
                bind_scalar(&mut (*cell).ptr_value as *mut _ as *mut *mut pg_sys::Node, cx);
            }
        }
        pg_sys::NodeTag::T_RelabelType => {
            bind_scalar(&mut (*(node as *mut pg_sys::RelabelType)).arg as *mut _ as *mut *mut pg_sys::Node, cx)
        }
        pg_sys::NodeTag::T_CoerceViaIO => {
            bind_scalar(&mut (*(node as *mut pg_sys::CoerceViaIO)).arg as *mut _ as *mut *mut pg_sys::Node, cx)
        }
        _ => {}
    }
    if cx.failed {
        return true;
    }
    pg_sys::expression_tree_walker_impl(node, Some(bind_walker), context)
}

unsafe fn bind_from(rte: *mut pg_sys::RangeTblEntry, cx: &mut StandIns) {
    if (*rte).rtekind != pg_sys::RTEKind::RTE_FUNCTION {
        return;
    }
    for i in 0..list_len((*rte).functions) {
        let rtfunc = list_ptr((*rte).functions, i) as *mut pg_sys::RangeTblFunction;
        let to_recordset = (*rtfunc).funcexpr as *mut pg_sys::FuncExpr;
        if to_recordset.is_null() || (*to_recordset).xpr.type_ != pg_sys::NodeTag::T_FuncExpr {
            continue;
        }
        let Some(idx) = marked(cx, (*to_recordset).location) else {
            continue;
        };
        let build_args = list_ptr((*to_recordset).args, 0) as *mut pg_sys::FuncExpr;
        if build_args.is_null() || (*build_args).xpr.type_ != pg_sys::NodeTag::T_FuncExpr {
            cx.failed = true;
            return;
        }
        let Some(call) = real_call(cx.calls[idx].funcid, (*build_args).args) else {
            cx.failed = true;
            return;
        };
        (*rtfunc).funcexpr = call;
        if !cx.calls[idx].keeps_coldefs {
            (*rtfunc).funccolnames = std::ptr::null_mut();
            (*rtfunc).funccoltypes = std::ptr::null_mut();
            (*rtfunc).funccoltypmods = std::ptr::null_mut();
            (*rtfunc).funccolcollations = std::ptr::null_mut();
        }
        (*rte).alias = cx.calls[idx].alias;
        cx.calls[idx].bound = true;
    }
}

unsafe fn bind_scalar(slot: *mut *mut pg_sys::Node, cx: &mut StandIns) {
    let node = *slot;
    if node.is_null() || (*node).type_ != pg_sys::NodeTag::T_CaseExpr {
        return;
    }
    let case = node as *mut pg_sys::CaseExpr;
    let Some(idx) = marked(cx, (*case).location) else {
        return;
    };
    let when = list_ptr((*case).args, 0) as *mut pg_sys::CaseWhen;
    let test = if when.is_null() { std::ptr::null_mut() } else { (*when).expr as *mut pg_sys::NullTest };
    let build_args = if test.is_null() { std::ptr::null_mut() } else { (*test).arg as *mut pg_sys::FuncExpr };
    if build_args.is_null() || (*build_args).xpr.type_ != pg_sys::NodeTag::T_FuncExpr {
        cx.failed = true;
        return;
    }
    let Some(call) = real_call(cx.calls[idx].funcid, (*build_args).args) else {
        cx.failed = true;
        return;
    };
    *slot = call;
    cx.calls[idx].bound = true;
}

/// `funcid(args)` with each argument coerced to its parameter type, as
/// ParseFuncOrColumn would build it.
unsafe fn real_call(funcid: pg_sys::Oid, args: *mut pg_sys::List) -> Option<*mut pg_sys::Node> {
    let mut argtypes: *mut pg_sys::Oid = std::ptr::null_mut();
    let mut nargs = 0;
    let rettype = pg_sys::get_func_signature(funcid, &mut argtypes, &mut nargs);
    if list_len(args) != nargs {
        return None;
    }
    let mut coerced = std::ptr::null_mut();
    for i in 0..nargs {
        let arg = list_ptr(args, i) as *mut pg_sys::Node;
        let target = *argtypes.add(i as usize);
        let arg = pg_sys::coerce_to_target_type(
            std::ptr::null_mut(),
            arg,
            pg_sys::exprType(arg),
            target,
            -1,
            pg_sys::CoercionContext::COERCION_IMPLICIT,
            pg_sys::CoercionForm::COERCE_IMPLICIT_CAST,
            -1,
        );
        if arg.is_null() {
            return None;
        }
        coerced = pg_sys::lappend(coerced, arg.cast());
    }
    let call = pg_sys::makeFuncExpr(
        funcid,
        rettype,
        coerced,
        pg_sys::InvalidOid,
        pg_sys::InvalidOid,
        pg_sys::CoercionForm::COERCE_EXPLICIT_CALL,
    );
    (*call).funcretset = pg_sys::get_func_retset(funcid);
    (*call).location = -1;
    pg_sys::assign_expr_collations(std::ptr::null_mut(), call.cast());
    Some(call.cast())
}

/// The function a plain `rvbbit.<name>(args)` call names, if it is a listed
/// fast path with exactly one definition of that arity and ordinary types.
unsafe fn fast_path_function(call: *mut pg_sys::FuncCall, name: &str) -> Option<pg_sys::Oid> {
    if !READER_FAST_PATHS.contains(&name)
        || !(*call).agg_order.is_null()
        || !(*call).agg_filter.is_null()
        || !(*call).over.is_null()
        || (*call).agg_within_group
        || (*call).agg_star
        || (*call).agg_distinct
        || (*call).func_variadic
    {
        return None;
    }
    let nargs = list_len((*call).args);
    for i in 0..nargs {
        let arg = list_ptr((*call).args, i) as *mut pg_sys::Node;
        if arg.is_null() || (*arg).type_ == pg_sys::NodeTag::T_NamedArgExpr {
            return None;
        }
    }
    let candidates: Vec<pg_sys::Oid> = pgrx::Spi::connect(|client| {
        let rows = client.select(
            "SELECT p.oid FROM pg_catalog.pg_proc p \
             JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace \
             WHERE n.nspname = 'rvbbit' AND p.proname = $1 AND p.pronargs = $2",
            None,
            &[name.into(), (nargs as i16).into()],
        )?;
        let mut out = Vec::new();
        for row in rows {
            if let Some(oid) = row.get::<pg_sys::Oid>(1)? {
                out.push(oid);
            }
        }
        Ok::<_, pgrx::spi::Error>(out)
    })
    .ok()?;
    let [funcid] = candidates[..] else {
        return None;
    };
    let mut argtypes: *mut pg_sys::Oid = std::ptr::null_mut();
    let mut declared = 0;
    pg_sys::get_func_signature(funcid, &mut argtypes, &mut declared);
    if (0..declared).any(|i| is_pseudo_type(*argtypes.add(i as usize))) {
        return None;
    }
    Some(funcid)
}

/// The function's name when `expr` is an rvbbit call or casts of one, which
/// is the column name PostgreSQL gives it (FigureColname).
unsafe fn cast_rvbbit_call_name(mut expr: *mut pg_sys::Node) -> Option<String> {
    while !expr.is_null() && (*expr).type_ == pg_sys::NodeTag::T_TypeCast {
        expr = (*(expr as *mut pg_sys::TypeCast)).arg;
    }
    if expr.is_null() || (*expr).type_ != pg_sys::NodeTag::T_FuncCall {
        return None;
    }
    rvbbit_call_name(expr.cast())
}

/// `name` when `call` is `rvbbit.<name>(...)`.
unsafe fn rvbbit_call_name(call: *mut pg_sys::FuncCall) -> Option<String> {
    let names = (*call).funcname;
    if list_len(names) != 2 {
        return None;
    }
    let schema = string_value(list_ptr(names, 0) as *mut pg_sys::Node)?;
    if schema != "rvbbit" {
        return None;
    }
    string_value(list_ptr(names, 1) as *mut pg_sys::Node)
}

unsafe fn make_call(name: &str, args: *mut pg_sys::List, location: i32) -> *mut pg_sys::FuncCall {
    let mut funcname = pg_sys::lappend(std::ptr::null_mut(), pg_sys::makeString(pstrdup("pg_catalog")).cast());
    funcname = pg_sys::lappend(funcname, pg_sys::makeString(pstrdup(name)).cast());
    pg_sys::makeFuncCall(funcname, args, pg_sys::CoercionForm::COERCE_EXPLICIT_CALL, location)
}

fn mark(idx: usize) -> i32 {
    FIRST_MARK - idx as i32
}

fn marked(cx: &StandIns, location: i32) -> Option<usize> {
    let idx = FIRST_MARK.checked_sub(location)?;
    (idx >= 0 && (idx as usize) < cx.calls.len()).then_some(idx as usize)
}

unsafe fn is_pseudo_type(typid: pg_sys::Oid) -> bool {
    typid != pg_sys::RECORDOID && pg_sys::get_typtype(typid) == pg_sys::TYPTYPE_PSEUDO as std::ffi::c_char
}

unsafe fn alias_colnames(alias: *mut pg_sys::Alias) -> Vec<String> {
    if alias.is_null() {
        return Vec::new();
    }
    (0..list_len((*alias).colnames))
        .filter_map(|i| string_value(list_ptr((*alias).colnames, i) as *mut pg_sys::Node))
        .collect()
}

unsafe fn string_value(node: *mut pg_sys::Node) -> Option<String> {
    if node.is_null() || (*node).type_ != pg_sys::NodeTag::T_String {
        return None;
    }
    let sval = (*(node as *mut pg_sys::String)).sval;
    (!sval.is_null()).then(|| CStr::from_ptr(sval).to_string_lossy().into_owned())
}

unsafe fn list_len(list: *mut pg_sys::List) -> i32 {
    if list.is_null() {
        0
    } else {
        (*list).length
    }
}

unsafe fn list_ptr(list: *mut pg_sys::List, i: i32) -> *mut c_void {
    if i >= list_len(list) {
        return std::ptr::null_mut();
    }
    (*(*list).elements.add(i as usize)).ptr_value
}

fn pstrdup(s: &str) -> *mut std::ffi::c_char {
    let s = CString::new(s).expect("identifier");
    unsafe { pg_sys::pstrdup(s.as_ptr()) }
}
