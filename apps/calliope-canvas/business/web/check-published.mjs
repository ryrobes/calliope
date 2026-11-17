// Validate real server snapshots with the official processor and web Vega engine.
// node check-published.mjs <snapshot-directory>
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {MessageProcessor, NodeResolver, getValue, isComponentNode} from '@a2ui/web_core/v0_9';
import {workspaceCatalog} from './catalog.js';
import {fitChart, validateChart} from './vega-chart.js';
import {compile} from 'vega-lite';
import {View, parse} from 'vega';
import {fieldError} from './fields.js';

const directory = process.argv[2], results = [];
for (const file of fs.readdirSync(directory).filter(f => f.endsWith('-snapshot.json'))) {
  const snapshot = JSON.parse(fs.readFileSync(path.join(directory, file), 'utf8'));
  const processor = new MessageProcessor([workspaceCatalog]);
  processor.processMessages(snapshot.messages);
  const surface = processor.getSurfaces().get('published-app');
  const resolver = new NodeResolver(surface, workspaceCatalog), seen = new Set();
  const walk = node => {
    assert.ok(isComponentNode(node), file);
    assert.equal(node.state, 'resolved', file + ': ' + node.componentId);
    seen.add(node.componentId);
    const props = getValue(node.props);
    for (const child of [...(props.children || []), ...(props.child ? [props.child] : [])]) walk(child);
  };
  walk(getValue(resolver.rootNode));
  assert.equal(seen.size, snapshot.messages[1].updateComponents.components.length, file);
  for (const node of snapshot.messages[1].updateComponents.components) {
    if (node.component === 'CField' && node.value?.path) {
      const value = surface.dataModel.get(node.value.path);
      if (value != null && String(value) !== '') assert.equal(fieldError(value, node), '', file + ': ' + node.id);
    }
  }
  resolver.dispose(); processor.dispose();
  console.log(file, seen.size, 'resolved nodes');
  if (file === 'charts-snapshot.json') {
    for (const [name, spec] of Object.entries(snapshot.messages[2].updateDataModel.value.charts)) {
      validateChart(spec);
      for (const width of [360, 1000]) {
        const view = new View(parse(compile(fitChart(spec, width, 440)).spec), {
          renderer: 'none', loader: {load: async () => {throw Error('Unexpected external chart data');}}
        });
        try {
          await view.runAsync();
          const svg = await view.toSVG();
          assert.ok(svg.includes('<svg') && svg.includes('role-mark'), name);
          fs.writeFileSync(path.join(directory, `${name}-${width}.svg`), svg);
          results.push({name, width, bytes: svg.length});
        } finally {view.finalize();}
      }
    }
  }
}
fs.writeFileSync(path.join(directory, 'web-charts.json'), JSON.stringify(results, null, 2));
console.log(results.length, 'web chart renders passed');
