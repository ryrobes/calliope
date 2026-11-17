import { useMemo, type ReactNode } from 'react'
import { PostgreSQL } from '@codemirror/lang-sql'
import { classHighlighter, highlightTree } from '@lezer/highlight'
import { formatDialect, postgresql } from 'sql-formatter'

/** Presentation only: the draft's original SQL remains the execution source. */
export function SqlPreview({sql}:{sql:string}) {
  const content = useMemo(() => {
    let formatted = sql
    try {
      formatted = formatDialect(sql, {dialect:postgresql, keywordCase:'upper', tabWidth:2})
    } catch { /* Incomplete or unsupported SQL must remain inspectable. */ }

    try {
      const tokens:ReactNode[] = []
      let cursor = 0
      highlightTree(PostgreSQL.language.parser.parse(formatted), classHighlighter, (from,to,classes) => {
        if (from > cursor) tokens.push(formatted.slice(cursor,from))
        tokens.push(<span key={from} className={classes}>{formatted.slice(from,to)}</span>)
        cursor = to
      })
      tokens.push(formatted.slice(cursor))
      return tokens
    } catch {
      return formatted
    }
  }, [sql])

  return <pre className="data-sql" aria-label="SQL query"><code>{content}</code></pre>
}
