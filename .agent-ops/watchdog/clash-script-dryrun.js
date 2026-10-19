const fs = require("fs");
const [, , scriptPath, inPath, outPath] = process.argv;
const cfg = JSON.parse(fs.readFileSync(inPath, "utf8"));
const main = new Function(fs.readFileSync(scriptPath, "utf8") + "\nreturn main;")();
const out = main(cfg, "VPN07.com");
for (const g of out["proxy-groups"]) console.log(g.name, g.type, "proxies=" + (g.proxies || []).length, "use=" + JSON.stringify(g.use || []), g.filter ? "filter=" + g.filter : "");
console.log("rules head:", out.rules.slice(0, 3).join(" | "), "... total", out.rules.length);
if (outPath) fs.writeFileSync(outPath, JSON.stringify(out));
