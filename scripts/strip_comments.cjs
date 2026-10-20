#!/usr/bin/env node
/* Strip the developer comments from a page as it is published.

   index.html is about a third comments, the notes that explain why the code
   is the way it is. They stay in the repository, where they are useful, and
   come out of the copy in _site, where every visitor would otherwise download
   them. scripts/build_site.sh runs this on _site/index.html.

   Three kinds, each removed by something that understands its syntax:
     - inline scripts (no type, or a JavaScript type) are re-emitted by the
       TypeScript compiler with removeComments, which parses the code rather
       than guessing at it, so a // inside a string or a regular expression is
       left alone. A block TypeScript reports a syntax error in is left exactly
       as it was. /*! comments are kept, as licence notices are.
     - inline styles lose their /* comments.
     - HTML comments outside scripts and styles go.

   JSON-LD and other data blocks are not touched. A re-emitted script that
   holds a bare closing script tag or an HTML comment opener, or more escaped
   closing tags than it had, is left as it was, so a re-emitted string can
   never end its script early.

   Usage: node scripts/strip_comments.cjs FILE [FILE ...]
   Needs the typescript package (pinned in the workflows); without it the
   files are left unchanged and it says so. */
'use strict';
const fs = require('fs');

let ts = null;
try { ts = require('typescript'); } catch (e) { /* reported below */ }

const JS_TYPES = /^(|text\/javascript|application\/javascript|module)$/i;

function stripJs(code) {
  const out = ts.transpileModule(code, {
    reportDiagnostics: true,
    compilerOptions: {
      removeComments: true,
      target: ts.ScriptTarget.ESNext,
      module: ts.ModuleKind.Preserve !== undefined ? ts.ModuleKind.Preserve : ts.ModuleKind.ESNext,
      allowJs: true,
      noEmitHelpers: true,
      importHelpers: false,
      useDefineForClassFields: true,
      verbatimModuleSyntax: false,
    },
    fileName: 'inline.js',
  });
  if (out.diagnostics && out.diagnostics.some(d => d.category === ts.DiagnosticCategory.Error)) return null;
  let js = out.outputText.replace(/\n?\/\/# sourceMappingURL=.*$/, '');
  // TypeScript adds `export {};` to a block it takes for a module; none of
  // these are, so a block that gains one is left as it was.
  if (/^\s*export\s*\{\s*\}\s*;?\s*$/m.test(js) && !/^\s*export\s*\{\s*\}/m.test(code)) return null;
  return js.trim();
}

function stripCss(css) {
  return css.replace(/\/\*(?!!)[\s\S]*?\*\//g, '').replace(/\n\s*\n+/g, '\n');
}

function count(s, re) { return (s.match(re) || []).length; }

function stripHtml(html) {
  const parts = [];
  const re = /<(script|style)\b([^>]*)>([\s\S]*?)<\/\1>/gi;
  let last = 0, m, kept = 0, changed = 0;
  const stripOuter = s => s.replace(/<!--(?!\[if)[\s\S]*?-->/g, '');
  while ((m = re.exec(html))) {
    parts.push(stripOuter(html.slice(last, m.index)));
    const [whole, tag, attrs, body] = m;
    let nbody = body;
    if (tag.toLowerCase() === 'style') {
      nbody = stripCss(body);
    } else {
      const type = (attrs.match(/\btype\s*=\s*["']?([^"'\s>]+)/i) || [, ''])[1];
      if (JS_TYPES.test(type) && !/\bsrc\s*=/.test(attrs) && body.trim()) {
        const js = stripJs(body);
        const esc = /<\\\/script/gi;
        if (js !== null && count(js, esc) <= count(body, esc) && !/<\/script/i.test(js) && count(js, /<!--/g) <= count(body, /<!--/g)) {
          nbody = '\n' + js + '\n';
          changed++;
        } else {
          kept++;
          if (process.env.STRIP_DEBUG) console.log("kept:", JSON.stringify(body.slice(0, 120)));
        }
      }
    }
    parts.push('<' + tag + attrs + '>' + nbody + '</' + tag + '>');
    last = m.index + whole.length;
  }
  parts.push(stripOuter(html.slice(last)));
  return { html: parts.join(''), changed, kept };
}

function main(files) {
  if (!ts) {
    console.warn('strip_comments: the typescript package is not installed, so the pages keep their comments');
    return 0;
  }
  for (const f of files) {
    const before = fs.readFileSync(f, 'utf8');
    const { html, changed, kept } = stripHtml(before);
    fs.writeFileSync(f, html);
    console.log('strip_comments: ' + f + ' ' + before.length + ' to ' + html.length + ' bytes, ' +
      changed + ' scripts stripped' + (kept ? ', ' + kept + ' left as they were' : ''));
  }
  return 0;
}

if (require.main === module) process.exit(main(process.argv.slice(2)));
module.exports = { stripHtml };
