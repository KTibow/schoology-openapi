// Build the static reference: one HTML page + the Scalar bundle + the spec.
//
// The whole site is the OpenAPI description. Prose that used to live in
// separate pages (authentication, conventions, realms) is in `info.description`
// instead, so it ships inside openapi.json and stays true for anyone consuming
// the spec directly rather than only for readers of this site. The same reason
// there is no generated Markdown mirror or access-matrix page: `x-student-access`
// and every description are already in the JSON, and anything that consumes this
// programmatically parses that rather than re-reading prose.
import { copyFile, mkdir, rm, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';

const require = createRequire(import.meta.url);
const root = new URL('.', import.meta.url).pathname;
const dist = join(root, 'dist');

// Pin the bundle by copying it out of node_modules rather than hot-linking a
// CDN: the published page then has no third-party runtime dependency.
// The package's `exports` map covers neither dist/ nor package.json, so derive
// the package root from its main entry (<root>/dist/index.js) instead.
const standalone = join(
  dirname(dirname(require.resolve('@scalar/api-reference'))),
  'dist/browser/standalone.js',
);

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });

await copyFile(join(root, 'src/index.html'), join(dist, 'index.html'));
await copyFile(standalone, join(dist, 'standalone.js'));

for (const name of ['openapi.json', 'openapi.strict.json']) {
  const from = join(root, '..', 'dist', name);
  try {
    await copyFile(from, join(dist, name));
  } catch (err) {
    if (err.code !== 'ENOENT') throw err;
    console.warn(`warning: ../dist/${name} missing — run scripts/build.py first`);
  }
}

// GitHub Pages would otherwise run the output through Jekyll.
await writeFile(join(dist, '.nojekyll'), '');

// standalone.js is redistributed verbatim, and MIT requires the copyright and
// permission notice to travel with it. @scalar/api-reference declares MIT in
// package.json but ships no LICENSE file, so write the notice out here. This is
// also what discharges the attribution obligation now that the "Powered by
// Scalar" badge is hidden -- MIT asks for the notice in the distribution, not
// for a link in the UI.
const scalarPkg = JSON.parse(
  await readFile(join(dirname(dirname(require.resolve('@scalar/api-reference'))), 'package.json'), 'utf8'),
);
await writeFile(
  join(dist, 'THIRD-PARTY-LICENSES.txt'),
  `standalone.js is @scalar/api-reference v${scalarPkg.version}, redistributed unmodified.
Source: https://github.com/scalar/scalar (packages/api-reference)

MIT License

Copyright (c) Scalar contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
`,
);

console.log('site/dist ready');
