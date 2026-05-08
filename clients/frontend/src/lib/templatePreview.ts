type TemplateToken =
  | { type: 'text'; text: string }
  | { type: 'variable'; path: string; modifiers: Array<[string, string | null]> }
  | { type: 'if'; condition: string }
  | { type: 'elif'; condition: string }
  | { type: 'else' }
  | { type: 'endif' }

type AstNode =
  | { type: 'text'; text: string }
  | { type: 'variable'; path: string; modifiers: Array<[string, string | null]> }
  | {
      type: 'if'
      condition: string
      trueBranch: AstNode[]
      elifBranches: Array<{ condition: string; body: AstNode[] }>
      falseBranch: AstNode[]
    }

const VARIABLE_PATTERN = /^\{([a-zA-Z_][a-zA-Z0-9_.]*(?:\|[^}]+)?)\}/
const IF_PATTERN = /^\{if\s+(.+?)\}/i
const ELIF_PATTERN = /^\{elif\s+(.+?)\}/i
const ELSE_PATTERN = /^\{else\}/i
const ENDIF_PATTERN = /^\{\/if\}/i

const FORBIDDEN_PATHS = new Set([
  '__class__',
  '__bases__',
  '__mro__',
  '__subclasses__',
  '__init__',
  '__new__',
  '__dict__',
  '__slots__',
  '__getattr__',
  '__setattr__',
  '__delattr__',
  '__getattribute__',
  '__globals__',
  '__code__',
  '__builtins__',
  '__import__',
  '__call__',
  '__reduce__',
  '__module__',
  '__weakref__',
  '__annotations__',
  '_sa_instance_state',
])

function smartSplit(input: string, delimiter: string): string[] {
  const parts: string[] = []
  let current = ''
  let depth = 0
  let inSingle = false
  let inDouble = false

  for (const char of input) {
    if (char === "'" && !inDouble) {
      inSingle = !inSingle
      current += char
      continue
    }
    if (char === '"' && !inSingle) {
      inDouble = !inDouble
      current += char
      continue
    }
    if (!inSingle && !inDouble) {
      if (char === '(') depth += 1
      if (char === ')') depth = Math.max(0, depth - 1)
      if (char === delimiter && depth === 0) {
        parts.push(current)
        current = ''
        continue
      }
    }
    current += char
  }

  if (current) parts.push(current)
  return parts
}

function parseVariable(content: string): { path: string; modifiers: Array<[string, string | null]> } {
  const parts = smartSplit(content, '|')
  const path = parts[0]?.trim() ?? ''
  const modifiers = parts.slice(1).map((modifier) => {
    const value = modifier.trim()
    const withArg = value.match(/^([a-zA-Z_]\w*)\((.*)\)$/)
    if (withArg) {
      return [withArg[1], withArg[2]] as [string, string]
    }
    return [value, null] as [string, null]
  })
  return { path, modifiers }
}

function tokenize(template: string): TemplateToken[] {
  const tokens: TemplateToken[] = []
  let position = 0

  while (position < template.length) {
    const input = template.slice(position)

    const ifMatch = input.match(IF_PATTERN)
    if (ifMatch) {
      tokens.push({ type: 'if', condition: ifMatch[1].trim() })
      position += ifMatch[0].length
      continue
    }

    const elifMatch = input.match(ELIF_PATTERN)
    if (elifMatch) {
      tokens.push({ type: 'elif', condition: elifMatch[1].trim() })
      position += elifMatch[0].length
      continue
    }

    const elseMatch = input.match(ELSE_PATTERN)
    if (elseMatch) {
      tokens.push({ type: 'else' })
      position += elseMatch[0].length
      continue
    }

    const endifMatch = input.match(ENDIF_PATTERN)
    if (endifMatch) {
      tokens.push({ type: 'endif' })
      position += endifMatch[0].length
      continue
    }

    const variableMatch = input.match(VARIABLE_PATTERN)
    if (variableMatch) {
      const parsed = parseVariable(variableMatch[1])
      tokens.push({ type: 'variable', path: parsed.path, modifiers: parsed.modifiers })
      position += variableMatch[0].length
      continue
    }

    if (template[position] === '{') {
      tokens.push({ type: 'text', text: '{' })
      position += 1
      continue
    }

    let end = position
    while (end < template.length && template[end] !== '{') {
      end += 1
    }
    tokens.push({ type: 'text', text: template.slice(position, end) })
    position = end
  }

  return tokens
}

class Parser {
  private index = 0
  private readonly tokens: TemplateToken[]

  constructor(tokens: TemplateToken[]) {
    this.tokens = tokens
  }

  parse(): AstNode[] {
    return this.parseBlock()
  }

  private parseBlock(options?: { stopAtElif?: boolean; stopAtElse?: boolean; stopAtEndIf?: boolean }): AstNode[] {
    const nodes: AstNode[] = []

    while (this.index < this.tokens.length) {
      const token = this.tokens[this.index]

      if (token.type === 'text') {
        nodes.push({ type: 'text', text: token.text })
        this.index += 1
        continue
      }

      if (token.type === 'variable') {
        nodes.push({ type: 'variable', path: token.path, modifiers: token.modifiers })
        this.index += 1
        continue
      }

      if (token.type === 'if') {
        this.index += 1
        const trueBranch = this.parseBlock({ stopAtElif: true, stopAtElse: true, stopAtEndIf: true })

        const elifBranches: Array<{ condition: string; body: AstNode[] }> = []
        while (this.index < this.tokens.length && this.tokens[this.index].type === 'elif') {
          const elifToken = this.tokens[this.index] as Extract<TemplateToken, { type: 'elif' }>
          this.index += 1
          const body = this.parseBlock({ stopAtElif: true, stopAtElse: true, stopAtEndIf: true })
          elifBranches.push({ condition: elifToken.condition, body })
        }

        let falseBranch: AstNode[] = []
        if (this.index < this.tokens.length && this.tokens[this.index].type === 'else') {
          this.index += 1
          falseBranch = this.parseBlock({ stopAtEndIf: true })
        }

        if (this.index < this.tokens.length && this.tokens[this.index].type === 'endif') {
          this.index += 1
        }

        nodes.push({
          type: 'if',
          condition: token.condition,
          trueBranch,
          elifBranches,
          falseBranch,
        })
        continue
      }

      if (token.type === 'elif' && options?.stopAtElif) break
      if (token.type === 'else' && options?.stopAtElse) break
      if (token.type === 'endif' && options?.stopAtEndIf) break

      this.index += 1
    }

    return nodes
  }
}

function getValueWithExists(path: string, context: Record<string, unknown>): { value: unknown; exists: boolean } {
  const parts = path.split('.')
  let value: unknown = context
  for (const part of parts) {
    if (FORBIDDEN_PATHS.has(part) || part.startsWith('_')) {
      return { value: null, exists: false }
    }
    if (typeof value === 'object' && value !== null && part in (value as Record<string, unknown>)) {
      value = (value as Record<string, unknown>)[part]
      continue
    }
    return { value: null, exists: false }
  }
  return { value, exists: true }
}

function getValue(path: string, context: Record<string, unknown>): unknown {
  return getValueWithExists(path, context).value
}

function resolveValue(expression: string, context: Record<string, unknown>): unknown {
  const normalized = expression.trim()
  if (!normalized) return ''

  if (
    (normalized.startsWith('"') && normalized.endsWith('"')) ||
    (normalized.startsWith("'") && normalized.endsWith("'"))
  ) {
    return normalized.slice(1, -1)
  }

  if (/^-?\d+(\.\d+)?$/.test(normalized)) {
    return normalized.includes('.') ? Number.parseFloat(normalized) : Number.parseInt(normalized, 10)
  }

  if (normalized.toLowerCase() === 'true') return true
  if (normalized.toLowerCase() === 'false') return false

  if (normalized.includes('.')) {
    const resolved = getValueWithExists(normalized, context)
    if (resolved.exists) return resolved.value
    return null
  }

  if (normalized in context) {
    return context[normalized]
  }

  return normalized
}

function isTruthy(value: unknown): boolean {
  if (value === null || value === undefined) return false
  if (typeof value === 'boolean') return value
  if (typeof value === 'string') return value.trim().length > 0
  if (Array.isArray(value)) return value.length > 0
  if (typeof value === 'number') return value !== 0
  return Boolean(value)
}

function compareValues(left: string, operator: string, right: string, context: Record<string, unknown>): boolean {
  const leftValue = resolveValue(left, context)
  const rightValue = resolveValue(right, context)

  const leftString = String(leftValue ?? '').toLowerCase()
  const rightString = String(rightValue ?? '').toLowerCase()

  switch (operator) {
    case '=':
      return leftString === rightString
    case '!=':
      return leftString !== rightString
    case '~':
      return leftString.includes(rightString)
    case '$':
      return leftString.startsWith(rightString)
    case '^':
      return leftString.endsWith(rightString)
    default:
      break
  }

  const leftNumber = Number(leftValue ?? 0)
  const rightNumber = Number(rightValue ?? 0)
  if (Number.isNaN(leftNumber) || Number.isNaN(rightNumber)) return false

  switch (operator) {
    case '>':
      return leftNumber > rightNumber
    case '<':
      return leftNumber < rightNumber
    case '>=':
      return leftNumber >= rightNumber
    case '<=':
      return leftNumber <= rightNumber
    default:
      return false
  }
}

function evaluateCondition(condition: string, context: Record<string, unknown>): boolean {
  const normalized = condition.trim()
  if (!normalized) return false

  const andParts = normalized.split(/\s+and\s+/i)
  if (andParts.length > 1) {
    return andParts.every((part) => evaluateCondition(part, context))
  }

  const orParts = normalized.split(/\s+or\s+/i)
  if (orParts.length > 1) {
    return orParts.some((part) => evaluateCondition(part, context))
  }

  if (/^not\s+/i.test(normalized)) {
    return !evaluateCondition(normalized.replace(/^not\s+/i, ''), context)
  }

  const operators = ['>=', '<=', '!=', '=', '>', '<', '~', '$', '^']
  for (const operator of operators) {
    const index = normalized.indexOf(operator)
    if (index !== -1) {
      const left = normalized.slice(0, index).trim()
      const right = normalized.slice(index + operator.length).trim()
      return compareValues(left, operator, right, context)
    }
  }

  return isTruthy(resolveValue(normalized, context))
}

function formatBytes(size: unknown): string {
  const bytes = Number(size ?? 0)
  if (!bytes || bytes <= 0 || Number.isNaN(bytes)) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let value = bytes
  let unitIndex = 0
  while (value >= 1000 && unitIndex < units.length - 1) {
    value /= 1000
    unitIndex += 1
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`
}

function formatTime(secondsInput: unknown): string {
  const seconds = Number(secondsInput ?? 0)
  if (!seconds || seconds <= 0 || Number.isNaN(seconds)) return ''
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remaining = Math.floor(seconds % 60)
  if (hours > 0)
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(remaining).padStart(2, '0')}`
  return `${String(minutes).padStart(2, '0')}:${String(remaining).padStart(2, '0')}`
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#x27;')
}

function applyModifier(value: unknown, modifier: string, arg: string | null): unknown {
  const name = modifier.toLowerCase()

  if (name === 'bytes') return formatBytes(value)
  if (name === 'time') return formatTime(value)
  if (name === 'upper') return String(value ?? '').toUpperCase()
  if (name === 'lower') return String(value ?? '').toLowerCase()
  if (name === 'title')
    return String(value ?? '').replace(/\w\S*/g, (word) => word[0].toUpperCase() + word.slice(1).toLowerCase())
  if (name === 'first') return Array.isArray(value) && value.length > 0 ? value[0] : ''
  if (name === 'last') return Array.isArray(value) && value.length > 0 ? value[value.length - 1] : ''
  if (name === 'length') return value ? (Array.isArray(value) || typeof value === 'string' ? value.length : 0) : 0
  if (name === 'exists') return value !== null && value !== undefined
  if (name === 'escape' || name === 'e') return escapeHtml(String(value ?? ''))

  if (name === 'join') {
    const separator = arg ? arg.trim().replace(/^['"]|['"]$/g, '') : ', '
    if (Array.isArray(value)) return value.map((entry) => String(entry)).join(separator)
    return String(value ?? '')
  }

  if (name === 'truncate') {
    const length = Number.parseInt(arg ?? '', 10)
    if (Number.isNaN(length) || length <= 0) return String(value ?? '')
    const text = String(value ?? '')
    return text.length > length ? `${text.slice(0, length)}...` : text
  }

  if (name === 'replace') {
    const raw = arg ?? ''
    const [beforeRaw, afterRaw = ''] = smartSplit(raw, ',')
    const before = beforeRaw?.trim().replace(/^['"]|['"]$/g, '') ?? ''
    const after = afterRaw?.trim().replace(/^['"]|['"]$/g, '') ?? ''
    return String(value ?? '').replaceAll(before, after)
  }

  return value
}

function renderNodes(nodes: AstNode[], context: Record<string, unknown>): string {
  const chunks: string[] = []

  for (const node of nodes) {
    if (node.type === 'text') {
      chunks.push(node.text)
      continue
    }

    if (node.type === 'variable') {
      let value = getValue(node.path, context)
      for (const [modifier, arg] of node.modifiers) {
        value = applyModifier(value, modifier, arg)
      }
      if (Array.isArray(value)) {
        chunks.push(value.map((entry) => String(entry)).join(', '))
      } else if (typeof value === 'boolean') {
        chunks.push('')
      } else {
        chunks.push(value === null || value === undefined ? '' : String(value))
      }
      continue
    }

    if (evaluateCondition(node.condition, context)) {
      chunks.push(renderNodes(node.trueBranch, context))
      continue
    }

    let matchedElif = false
    for (const branch of node.elifBranches) {
      if (evaluateCondition(branch.condition, context)) {
        chunks.push(renderNodes(branch.body, context))
        matchedElif = true
        break
      }
    }

    if (!matchedElif) {
      chunks.push(renderNodes(node.falseBranch, context))
    }
  }

  const output = chunks.join('')
  return output
    .split('\n')
    .filter((line) => line.trim().length > 0)
    .join('\n')
}

/**
 * Render MediaFusion stream template syntax in the browser for live previews.
 * This mirrors backend behavior closely enough for configuration UX feedback.
 */
export function renderTemplatePreview(template: string, context: Record<string, unknown>): string {
  const tokens = tokenize(template)
  const parser = new Parser(tokens)
  const ast = parser.parse()
  return renderNodes(ast, context)
}
