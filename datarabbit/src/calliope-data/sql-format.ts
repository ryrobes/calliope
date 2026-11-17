import { formatDialect, postgresql } from 'sql-formatter'

/** Format incoming SQL with the same dialect/options as the workbench button.
 * Unsupported RVBBIT syntax stays intact and executable. */
export function formatIncomingSql(sql:string):string {
  try { return formatDialect(sql,{dialect:postgresql,keywordCase:'upper',tabWidth:2}) }
  catch { return sql }
}
