const assert = require('node:assert/strict')
const { test } = require('node:test')
const { pdfFileName } = require('../src/pdf.cjs')

test('the PDF is named after the session', () => {
  assert.equal(pdfFileName('Physics: loops'), 'Physics_ loops.pdf')
  assert.equal(pdfFileName('Étape ✓ 収益'), 'Étape ✓ 収益.pdf')
})

test('names that could escape the folder or that systems reject are made safe', () => {
  assert.equal(pdfFileName('../../etc/passwd'), '_.._etc_passwd.pdf')
  assert.equal(pdfFileName('a\u0000b\nc'), 'abc.pdf')
  assert.equal(pdfFileName('...hidden'), 'hidden.pdf')
  assert.equal(pdfFileName('x'.repeat(300)).length, 124)
})

test('an empty name gets a plain one', () => {
  for (const t of ['', '   ', null, undefined, '...']) assert.equal(pdfFileName(t), 'Iris notes.pdf')
})
