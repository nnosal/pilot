// Catppuccin Mocha palette for ANSI colours
const ANSI_FG = {
  30: '#45475a', 31: '#f38ba8', 32: '#a6e3a1', 33: '#f9e2af',
  34: '#89b4fa', 35: '#cba6f7', 36: '#89dceb', 37: '#cdd6f4',
  90: '#585b70', 91: '#f38ba8', 92: '#a6e3a1', 93: '#f9e2af',
  94: '#89b4fa', 95: '#cba6f7', 96: '#89dceb', 97: '#ffffff',
}

export function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

export function ansiToHtml(text) {
  let html = ''
  let openSpans = 0
  for (const part of text.split(/(\x1b\[[0-9;]*[A-Za-z])/)) {
    if (part.startsWith('\x1b[') && part.endsWith('m')) {
      for (const code of part.slice(2, -1).split(';')) {
        if (code === '0' || code === '') {
          html += '</span>'.repeat(openSpans)
          openSpans = 0
        } else if (code === '1') {
          html += '<span style="font-weight:bold">'; openSpans++
        } else if (ANSI_FG[code]) {
          html += `<span style="color:${ANSI_FG[code]}">`;  openSpans++
        }
      }
    } else if (!part.startsWith('\x1b[')) {
      html += escapeHtml(part)
    }
  }
  return html + '</span>'.repeat(openSpans)
}

// Resolve \r (progress-bar overwrites): keep the last non-whitespace segment
function applyCarriageReturns(raw) {
  const parts = raw.split('\r')
  for (let i = parts.length - 1; i >= 0; i--) {
    if (parts[i].trim()) return parts[i].trimEnd()
  }
  return ''
}

// Line-level heuristics for tools that never emit ANSI when stdout isn't a
// tty (mise tasks, bench, git …). Order matters: line-anchored rules first,
// then in-line keyword rules over whatever text remains unwrapped.
const LINE_RULES = [
  [/^(\[[\w:.-]+\])/, '#89b4fa;font-weight:600'], // [step:name] marker
  [/^(\$ )/, '#6c7086'], // shell command echo
  [/^(- )/, '#89dceb'], // mef task summary line
  [/^(⚠.*)$/, '#f9e2af'], // warning line
]
const KEYWORD_RULES = [
  [/\b(ERROR|Error|FAILED|Failed)\b/g, '#f38ba8;font-weight:600'],
  [/\b(SUCCESS|Success)\b/g, '#a6e3a1;font-weight:600'],
]

function highlightPlainLine(text) {
  let html = escapeHtml(text)
  for (const [re, style] of LINE_RULES) {
    if (re.test(html)) {
      html = html.replace(re, `<span style="color:${style}">$1</span>`)
      break
    }
  }
  for (const [re, style] of KEYWORD_RULES) {
    html = html.replace(re, `<span style="color:${style}">$1</span>`)
  }
  return html
}

export function processLine(raw) {
  const resolved = applyCarriageReturns(raw)
  return resolved.includes('\x1b[') ? ansiToHtml(resolved) : highlightPlainLine(resolved)
}
