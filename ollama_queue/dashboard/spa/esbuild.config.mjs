import * as esbuild from 'esbuild';
import { createHash } from 'crypto';
import { readFileSync, writeFileSync } from 'fs';

const isWatch = process.argv.includes('--watch');

const config = {
  entryPoints: ['src/index.jsx'],
  bundle: true,
  outfile: 'dist/bundle.js',
  format: 'esm',
  jsx: 'transform',
  jsxFactory: 'h',
  jsxFragment: 'Fragment',
  inject: ['./src/preact-shim.js'],
  loader: { '.jsx': 'jsx' },
  minify: !isWatch,
  sourcemap: isWatch,
  logLevel: 'info',
};

function injectVersionHash() {
  const js  = readFileSync('dist/bundle.js');
  const css = readFileSync('dist/bundle.css');
  const hash = createHash('sha256')
    .update(js).update(css)
    .digest('hex')
    .slice(0, 8);
  const html = readFileSync('index.html', 'utf8')
    .replace('bundle.css"', `bundle.css?v=${hash}"`)
    .replace('bundle.js"',  `bundle.js?v=${hash}"`);
  writeFileSync('dist/index.html', html);
  console.log(`Cache hash: ${hash}`);
}

if (isWatch) {
  const ctx = await esbuild.context(config);
  await ctx.watch();
  console.log('esbuild watching for changes...');
} else {
  await esbuild.build(config);
  injectVersionHash();
}
