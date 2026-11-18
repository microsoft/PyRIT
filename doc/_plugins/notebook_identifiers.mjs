// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const docRoot = fileURLToPath(new URL('../', import.meta.url));

function cellIdentifier(file, identifier) {
  const relative = path.relative(docRoot, file).split(path.sep).join('/');
  return `notebook-${createHash('sha256').update(relative).digest('hex')}-${identifier}`;
}

function visit(node, callback) {
  callback(node);
  node.children?.forEach((child) => visit(child, callback));
}

function notebookIdentifiers() {
  return (tree, file) => {
    const replacements = new Map();
    if (path.extname(file.path) === '.ipynb') {
      visit(tree, (block) => {
        if (block.type !== 'block' || !/^\d+$/.test(block.html_id)) return;
        // Captioned cells move their reference target onto the container.
        const targets = [block, ...(block.children ?? []).filter((node) => node.type === 'container')];
        for (const target of targets) {
          if (target.label != null || target.identifier !== block.html_id) continue;
          const identifier = cellIdentifier(file.path, target.identifier);
          replacements.set(target.identifier, identifier);
          target.html_id ??= target === block ? block.html_id : `id-${block.html_id}`;
          target.identifier = identifier;
        }
      });
    }

    visit(tree, (node) => {
      if (node.type === 'crossReference' && replacements.has(node.identifier)) {
        node.identifier = replacements.get(node.identifier);
      }
      if (node.type !== 'link' || typeof node.url !== 'string') return;
      if (node.url.startsWith('#')) {
        const identifier = replacements.get(node.url.slice(1));
        if (identifier) node.url = `#${identifier}`;
        return;
      }
      if (node.url.startsWith('//')) return;
      const match = node.url.match(/^([^:#?]+)#(\d+)$/);
      if (!match) return;
      const destination = decodeURIComponent(match[1]);
      let targetFile = destination.startsWith('/')
        ? path.resolve(docRoot, destination.slice(1))
        : path.resolve(path.dirname(file.path), destination);
      if (!path.extname(targetFile) && !existsSync(targetFile)) {
        // MyST prefers a Markdown source over a notebook for extensionless links.
        if (existsSync(`${targetFile}.md`)) return;
        targetFile += '.ipynb';
      }
      if (path.extname(targetFile) !== '.ipynb') return;
      let notebook;
      try {
        notebook = JSON.parse(readFileSync(targetFile, 'utf8'));
      } catch (error) {
        if (error.code === 'ENOENT') return; // Let MyST report the missing destination.
        throw error;
      }
      const cell = notebook.cells.find((entry) => entry.id === match[2]);
      if (!cell || typeof cell.metadata?.label === 'string') return;
      node.url = `${match[1]}#${cellIdentifier(targetFile, match[2])}`;
    });
  };
}

export default {
  name: 'notebook-cell-identifiers',
  transforms: [
    { name: 'notebook-cell-identifiers', stage: 'document', plugin: notebookIdentifiers },
  ],
};
