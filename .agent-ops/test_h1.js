const fs = require('fs');
const code = fs.readFileSync('docs/dashboard-platform/shell/plugins/path-panel.js', 'utf-8');

const elements = {};
function mockElement(id) {
  return {
    id: id,
    style: {},
    parentElement: { offsetWidth: 800, offsetHeight: 600 },
    width: 800,
    height: 600,
    getContext: () => ctx,
    addEventListener: () => {},
    querySelectorAll: () => [],
    innerHTML: '',
    textContent: ''
  };
}

const drawnText = [];
let strokeCount = 0;
let lineToPoints = [];

const ctx = {
  fillStyle: '',
  fillRect: () => {},
  beginPath: () => {},
  moveTo: () => {},
  lineTo: (x, y) => { lineToPoints.push({x, y}); },
  stroke: () => { strokeCount++; },
  arc: () => {},
  fill: () => {},
  setLineDash: () => {},
  fillText: (t, x, y) => { drawnText.push({text: t, x, y}); }
};

const dom = {
  getElementById: (id) => {
    if (!elements[id]) elements[id] = mockElement(id);
    return elements[id];
  },
  querySelectorAll: () => []
};

let registeredInit = null;
global.window = {
  addEventListener: () => {},
  __registerPlugin__: (name, init, destroy) => { registeredInit = init; }
};
global.document = dom;

eval(code);

let subscriber = null;
const api = {
  registerPanel: (name, cb) => {
    const container = mockElement('container');
    cb(container);
  },
  subscribe: (cb) => { subscriber = cb; }
};

registeredInit(api);

console.log("=== H1 EMPTY STATE TEST ===");
console.log("Canvas drawn text:", JSON.stringify(drawnText));
console.log("Badge text:", elements['pp-demo-badge'].textContent);
console.log("Badge display:", elements['pp-demo-badge'].style.display);
console.log("Trail lineTo points:", lineToPoints.length);

// Feed empty payload (no position keys)
drawnText.length = 0;
subscriber({ streams: { "0": { values: { "status.arm": 1 } } } });
console.log("After empty stream update - drawn text:", JSON.stringify(drawnText));
console.log("After empty stream update - badge text:", elements['pp-demo-badge'].textContent);
console.log("After empty stream update - trail points:", lineToPoints.length);

console.log("\n=== H1 LIVE FRAME C TEST ===");
// Feed synthetic Frame C (tag c -> slot 3 in state.streams)
drawnText.length = 0;
lineToPoints.length = 0;
subscriber({
  streams: {
    "3": {
      values: {
        "c.earth_x": 42.5,
        "c.earth_y": -18.25,
        "c.altitude": 1.75
      }
    }
  }
});
console.log("After Frame C point 1 (42.5, -18.25):");
console.log("Badge display:", elements['pp-demo-badge'].style.display);
console.log("Metrics HTML:", elements['pp-metrics'].innerHTML);

// Feed second point
subscriber({
  streams: {
    "3": {
      values: {
        "c.earth_x": 45.0,
        "c.earth_y": -15.0,
        "c.altitude": 2.0
      }
    }
  }
});
console.log("After Frame C point 2 (45.0, -15.0):");
console.log("Drawn lineTo count:", lineToPoints.length);
console.log("Plotted lineTo points (screen coords):");
console.log(JSON.stringify(lineToPoints));
console.log("Metrics HTML:", elements['pp-metrics'].innerHTML);
