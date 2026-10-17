// Clash Verge profile script for VPN07.com (profile R8glH1bJTsOP). Rewritten 2026-10-18 by Claude Code.
// Previous version: sGvyrA9fCj9s.js.bak-2026-10-18 (JP/SG-only auto groups, no AI routing).
//
// What it does:
//  1. Pools the 月神云 nodes (file provider) into every group, as before.
//  2. One auto-switching group per country (US-auto, JP-auto, SG-auto, JPSG-auto). Each one only ever
//     switches between nodes of its own country, so the exit country (and Google's view of it) never changes.
//  3. "AI-exit" is the country picker: a selector whose default is US-auto. Google (Gemini/Argon/Jules/Kaggle/
//     Colab), Anthropic (Claude) and OpenAI traffic is routed to it by rules prepended in front of the
//     subscription's rules. Pick another country there and the auto-switching follows that country.
//  4. 自动选择 / 故障转移 (all other traffic) stay JP/SG only, which is the user's general preference.
//
// Switching is kept calm on purpose: re-test every 180 s, move only when the current node fails or another node
// of the same country is more than 150 ms faster. Moving does not drop open connections; only new ones use the
// new node. Region patterns are case-sensitive: /US/i would also match Australia, Russia, Belarus and Cyprus.
const OTHER = "月神云";
const OTHER_FILE = "./profiles/Rkhp8Dd5BhV7.yaml";
const INFO_NODES = /剩余流量|距离下次重置|套餐到期|官网/;
const CHECK_URL = "http://www.gstatic.com/generate_204";
const AI_GROUP = "AI-exit";
const REGIONS = [
  ["US-auto", "\\bUS\\b|USA|United States|美国|洛杉矶|圣何塞|硅谷|西雅图|纽约|芝加哥|达拉斯"],
  ["JP-auto", "\\bJP\\b|Japan|日本|东京|大阪|Tokyo|Osaka"],
  ["SG-auto", "\\bSG\\b|Singapore|新加坡|狮城"],
];
const JPSG = REGIONS[1][1] + "|" + REGIONS[2][1];
const ALL_REGIONS = REGIONS.map((r) => r[1]).join("|");
const AI_RULES = [
  "DOMAIN-SUFFIX,googlevideo.com,VPN07.com", // YouTube video bytes: keep them off the AI exit
  "DOMAIN-KEYWORD,google", // google.com, googleapis.com, *.google (Antigravity, Gemini, Jules, Colab)
  "DOMAIN-SUFFIX,gstatic.com",
  "DOMAIN-SUFFIX,kaggle.com",
  "DOMAIN-SUFFIX,kaggle.io",
  "DOMAIN-KEYWORD,codeium", // Antigravity backend heritage
  "DOMAIN-KEYWORD,windsurf",
  "DOMAIN-SUFFIX,anthropic.com",
  "DOMAIN-SUFFIX,claude.ai",
  "DOMAIN-SUFFIX,claude.com",
  "DOMAIN-SUFFIX,claudeusercontent.com",
  "DOMAIN-SUFFIX,openai.com",
  "DOMAIN-SUFFIX,chatgpt.com",
  "DOMAIN-SUFFIX,oaistatic.com",
  "DOMAIN-SUFFIX,oaiusercontent.com",
].map((r) => (r.split(",").length === 2 ? r + "," + AI_GROUP : r));

function matching(names, pattern) {
  const re = new RegExp(pattern);
  return names.filter((n) => re.test(n) && !INFO_NODES.test(n));
}

function autoGroup(name, pattern, names, interval, tolerance) {
  return {
    name,
    type: "url-test",
    proxies: matching(names, pattern),
    use: [OTHER],
    filter: pattern,
    "exclude-filter": INFO_NODES.source,
    url: CHECK_URL,
    interval,
    tolerance,
    timeout: 3000,
    lazy: false,
    "max-failed-times": 3,
  };
}

function main(config, profileName) {
  const names = (config.proxies || []).map((p) => p.name);
  config["proxy-providers"] = Object.assign({}, config["proxy-providers"], {
    [OTHER]: {
      type: "file",
      path: OTHER_FILE,
      interval: 3600,
      "exclude-filter": INFO_NODES.source,
      override: { "additional-prefix": OTHER + " | " },
      "health-check": { enable: true, url: CHECK_URL, interval: 300 },
    },
  });

  const regionGroups = REGIONS.map(([name, pattern]) => autoGroup(name, pattern, names, 180, 150));
  regionGroups.push(autoGroup("JPSG-auto", JPSG, names, 180, 150));
  const aiExit = {
    name: AI_GROUP,
    type: "select",
    proxies: regionGroups.map((g) => g.name).concat(matching(names, ALL_REGIONS)),
    use: [OTHER],
    filter: ALL_REGIONS, // fixed nodes of all three countries: the vpn_monitor tray pins one when auto-switch is off
    "exclude-filter": INFO_NODES.source,
  };

  const groups = config["proxy-groups"] || [];
  for (const group of groups) {
    if (!["VPN07.com", "自动选择", "故障转移"].includes(group.name)) continue;
    group.use = (group.use || []).concat(OTHER);
    if (group.type === "select") {
      const regionNames = regionGroups.map((g) => g.name);
      const rest = (group.proxies || []).filter((n) => !regionNames.includes(n));
      group.proxies = rest.slice(0, 2).concat(regionNames, rest.slice(2));
      continue;
    }
    group.proxies = matching(group.proxies || [], JPSG);
    group.filter = JPSG;
    group["exclude-filter"] = INFO_NODES.source;
    group.url = CHECK_URL;
    group.interval = 300;
    group.tolerance = 100;
  }
  config["proxy-groups"] = [aiExit].concat(regionGroups, groups);
  config.rules = AI_RULES.concat(config.rules || []);
  return config;
}
