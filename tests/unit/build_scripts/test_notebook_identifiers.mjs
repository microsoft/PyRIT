// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';

import plugin from '../../../doc/_plugins/notebook_identifiers.mjs';

const transform = plugin.transforms[0].plugin();

test('fallback targets and local refs change once, preserving explicit labels and HTML IDs', () => {
  const tree = { children: [
    { type: 'block', identifier: '0', html_id: '0' },
    { type: 'block', html_id: '1', children: [{ type: 'container', identifier: '1' }] },
    { type: 'block', identifier: '3', html_id: '3', label: '3' },
    { type: 'block', identifier: 'authored', html_id: 'custom-anchor' },
    { type: 'crossReference', identifier: '0' },
    { type: 'link', url: '#1' },
    { type: 'link', url: '#3' },
  ] };
  transform(tree, { path: path.resolve('one.ipynb') });
  const [block, caption, numericLabel, authored, reference, link, explicitLink] = tree.children;
  assert.match(block.identifier, /^notebook-[a-f0-9]{64}-0$/);
  assert.equal(reference.identifier, block.identifier);
  assert.equal(block.html_id, '0');
  assert.equal(caption.children[0].html_id, 'id-1');
  assert.equal(link.url, `#${caption.children[0].identifier}`);
  assert.equal(explicitLink.url, '#3');
  assert.equal(numericLabel.identifier, '3');
  assert.equal(authored.identifier, 'authored');
  assert.equal(authored.html_id, 'custom-anchor');
  const first = structuredClone(tree);
  transform(tree, { path: path.resolve('one.ipynb') });
  assert.deepEqual(tree, first);
});

test('only existing notebook fallback cells are rewritten, with MyST Markdown precedence', () => {
  const folder = mkdtempSync(path.join(tmpdir(), 'pyrit-notebook-refs-'));
  try {
    writeFileSync(path.join(folder, 'one.ipynb'), JSON.stringify({ cells: [
      { id: '0', metadata: {} }, { id: '4', metadata: { label: '4' } },
    ] }));
    const urls = ['one.ipynb#0', 'one#0', 'one.ipynb#4', 'one.ipynb#missing',
      'missing.ipynb#0', 'https://example.org/one.ipynb#0', '//example.org/one.ipynb#0'];
    const tree = { children: urls.map((url) => ({ type: 'link', url })) };
    transform(tree, { path: path.join(folder, 'index.md') });
    assert.match(tree.children[0].url, /^one\.ipynb#notebook-[a-f0-9]{64}-0$/);
    assert.equal(tree.children[1].url.split('#')[1], tree.children[0].url.split('#')[1]);
    assert.deepEqual(tree.children.slice(2).map((node) => node.url), urls.slice(2));
    writeFileSync(path.join(folder, 'one.md'), '# Markdown takes precedence');
    const markdown = { children: [{ type: 'link', url: 'one#0' }] };
    transform(markdown, { path: path.join(folder, 'index.md') });
    assert.equal(markdown.children[0].url, 'one#0');
  } finally {
    rmSync(folder, { recursive: true });
  }
});

test('invalid destination notebooks surface their parse error', () => {
  const folder = mkdtempSync(path.join(tmpdir(), 'pyrit-notebook-invalid-'));
  try {
    writeFileSync(path.join(folder, 'broken.ipynb'), '{invalid');
    assert.throws(() => transform(
      { children: [{ type: 'link', url: 'broken.ipynb#0' }] },
      { path: path.join(folder, 'index.md') },
    ), SyntaxError);
  } finally {
    rmSync(folder, { recursive: true });
  }
});
