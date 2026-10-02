import { test } from 'node:test'
import assert from 'node:assert/strict'
import { TAGS, CELLS, normalizeCell, tagsForCell } from './requirementTags.ts'

const EXPECTED: Record<string, string[]> = {
  'GE: 2': ['GE:2'], 'GE: UD 4': ['GE:UD4'], 'GE: UD 3': ['GE:UD3'], 'GE: 4': ['GE:4'],
  'GE: 3A': ['GE:3A'], 'GE: 1C': ['GE:1C'], 'GE: UD 2/5': ['GE:UD2', 'GE:UD5'],
  'GE: 3B': ['GE:3B'], 'GE: 1A': ['GE:1A'], 'GE: 6': ['GE:6'], 'GE: 1B': ['GE:1B'],
  'GE: E': ['LGE:E'], 'GE: 5A': ['GE:5A'], 'GE: 5B': ['GE:5B'], 'GE: 5C': ['GE:5C'],
  'GE: Area 4': ['GE:4'], 'GE: 5A+5C': ['GE:5A', 'GE:5C'], 'GE: WID+3': ['WID', 'GE:3'],
  'GE: 4+US23': ['GE:4', 'AI:US2', 'AI:US3'], 'GE: 4+US1': ['GE:4', 'AI:US1'],
  'GE: 5B+5C': ['GE:5B', 'GE:5C'], 'GE: 3B+US1': ['GE:3B', 'AI:US1'],
  'GE: 3+US23': ['GE:3', 'AI:US2', 'AI:US3'], 'GE: 4+US': ['GE:4'],
  'GE:3B+US23': ['GE:3B', 'AI:US2', 'AI:US3'], 'GE: 1Bor4': ['GE:1B', 'GE:4'],
  'PE: PhysEd': ['PE'], WID: ['WID'], GWAR: ['GWAR'], 'AI: US1': ['AI:US1'], 'AI: US3': ['AI:US3'],
}

test('all 31 measured spellings map as specified', () => {
  assert.equal(Object.keys(EXPECTED).length, 31)
  assert.equal(Object.keys(CELLS).length, 31)
  for (const [raw, tags] of Object.entries(EXPECTED)) {
    const r = tagsForCell(raw)
    assert.deepEqual(r.tags, tags, raw)
    assert.equal(r.known, true, raw)
    assert.equal(r.raw, raw)
  }
})

test('normalizeCell trims, collapses whitespace and drops space after colon', () => {
  assert.equal(normalizeCell('  GE:   UD   3 '), 'GE:UD 3')
  assert.equal(normalizeCell('GE: 3B+US23'), normalizeCell('GE:3B+US23'))
  assert.equal(normalizeCell('GE:\t4'), 'GE:4')
  assert.equal(normalizeCell('ge: 4'), 'ge:4')
})

test('no-space colon form resolves like the spaced form', () => {
  assert.deepEqual(tagsForCell('GE:3B+US23').tags, tagsForCell('GE: 3B+US23').tags)
  assert.deepEqual(tagsForCell('GE:2').tags, ['GE:2'])
})

test('unknown cells return known false, no tags, raw kept', () => {
  for (const raw of ['', 'GE: 9Z', 'toString', 'GE: US']) {
    assert.deepEqual(tagsForCell(raw), { tags: [], raw, known: false })
  }
})

test('tag ids are unique', () => {
  const ids = TAGS.map((t) => t.id)
  assert.equal(new Set(ids).size, ids.length)
})

test('every id used in cells exists in tags; cell keys are normalised', () => {
  const ids = new Set(TAGS.map((t) => t.id))
  for (const [key, tags] of Object.entries(CELLS)) {
    assert.equal(normalizeCell(key), key)
    for (const t of tags) assert.ok(ids.has(t), `${key} -> ${t}`)
  }
})
