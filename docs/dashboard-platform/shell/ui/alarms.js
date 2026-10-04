/* alarms.js: the one alarm registry and engine of the dashboard (WP-39, WP-32 D2 + D3); exposes window.GSAlarms.
 * index.html loads it after ui-kit.js and before any plugin. The sidebar Alarms card (shell) and the Overview panel
 * read the same shared engine (GSAlarms.shared()), so the two surfaces cannot disagree.
 *
 * Levels (status scale in ui/tokens.css):
 *   warning   red    act now   alarm row with its required action; spoken once per episode (D3)
 *   caution   amber  act soon  alarm row with its required action
 *   advisory  blue   log only  goes to the episode log, never to an alarm row, the banner or the voice
 * A warning or caution rule that does not name its required action is not an alarm: validate() refuses it.
 *
 * Debounce: a condition raises after `n` consecutive samples that hold it and clears after `n` samples without it.
 * A sample is one /state snapshot. The shell and the Overview both call update() with the same state object on every
 * poll; the second call inside SAMPLE_MS is ignored. A frozen state counts again after SAMPLE_MS, so time-based rules
 * (link lost) still fire when the /state poll dies.
 * "May fly" = status.arm is 1 or not published: the same condition is a warning while the aircraft may be in the
 * air and a caution while it is known to be disarmed (WP-32: low battery airborne is act now, on the bench it is not).
 * Thresholds live in LIMITS with their sources; the debounce counts `n` are PROPOSED (not measured on the aircraft). */
(function (root) {
  'use strict';

  var LEVELS = {
    warning:  { rank: 3, status: 'fail', sev: 'red',   label: 'WARNING',  shown: true,  speak: true },
    caution:  { rank: 2, status: 'warn', sev: 'amber', label: 'CAUTION',  shown: true,  speak: false },
    advisory: { rank: 1, status: 'info', sev: 'info',  label: 'ADVISORY', shown: false, speak: false }
  };

  var LIMITS = {
    vbatLowV: 15.0,        // firmware low-battery beep, StabilizerTask.c:1329 (was overview VBAT_RED_V)
    vbatCautionV: 15.5,    // dashboard early warning, 0.5 V above the beep (was overview VBAT_AMBER_V)
    lossCautionPct: 5.0,   // plugin-api.md loss table: above 5 % was red in the shell and the Overview
    lossAdvisoryPct: 1.0,  // same table: above 1 % was amber
    slowMs: 2000,          // Overview STALE_WARN_MS: a slot this old is slow
    linkLostMs: 3000,      // shell rule "No telemetry update in 3+ seconds"
    ttlMs: 30000           // service default slot_freshness_ttl_ns, used when /state does not send it
  };
  var SAMPLE_MS = 900;
  var LOG_MAX = 500;
  var SLOT_ORDER = ['0', '1', '3', '2'];

  function fmtAge(ms) {
    if (ms < 10000) return (Math.max(0, ms) / 1000).toFixed(1) + ' s';
    if (ms < 120000) return Math.round(ms / 1000) + ' s';
    return Math.round(ms / 60000) + ' min';
  }

  // ── context the rules read: one per sample ────────────────────────────────────────────────────────────
  function makeContext(state, nowMs) {
    var streams = (state && state.streams) || {};
    var slots = Object.keys(streams);
    var order = SLOT_ORDER.filter(function (n) { return slots.indexOf(n) >= 0; })
      .concat(slots.filter(function (n) { return SLOT_ORDER.indexOf(n) < 0; }));
    var newest = null;
    slots.forEach(function (n) {
      var s = streams[n];
      if (s && s.last_update_ns && (newest === null || s.last_update_ns > newest)) newest = s.last_update_ns;
    });
    function value(key) {
      for (var i = 0; i < order.length; i++) {
        var s = streams[order[i]];
        var v = s && s.values ? s.values[key] : null;
        if (v !== null && v !== undefined && !isNaN(Number(v))) return Number(v);
      }
      return null;
    }
    var arm = value('status.arm');
    return {
      nowMs: nowMs, streams: streams, slots: order, value: value,
      ttlMs: state && state.slot_freshness_ttl_ns ? state.slot_freshness_ttl_ns / 1e6 : LIMITS.ttlMs,
      newestMs: newest === null ? null : newest / 1e6,
      mayFly: arm === null || arm !== 0,
      slotAge: function (n) {
        var s = streams[n];
        return s && s.last_update_ns ? Math.max(0, nowMs - s.last_update_ns / 1e6) : null;
      }
    };
  }

  // ── rule checks: (ctx[, slot]) -> text while the condition holds, else null ───────────────────────────
  function linkDown(c) {
    if (!c.slots.length || c.newestMs === null) return null;
    var age = c.nowMs - c.newestMs;
    return age > LIMITS.linkLostMs ? 'No telemetry for ' + fmtAge(age) + ': link lost' : null;
  }
  function vbatLow(c) {
    var v = c.value('status.vbat');
    return v !== null && v < LIMITS.vbatLowV ?
      'BATTERY LOW: ' + v.toFixed(2) + ' V (firmware beep threshold ' + LIMITS.vbatLowV.toFixed(1) + ' V)' : null;
  }
  function rcLost(c) {
    var v = c.value('status.sbus_lost');
    return v !== null && v !== 0 ? 'RC RECEIVER LINK LOST (status.sbus_lost = ' + v + ')' : null;
  }
  function flying(check) { return function (c) { return c.mayFly ? check(c) : null; }; }
  function grounded(check) { return function (c) { return c.mayFly ? null : check(c); }; }

  var RULES_ROWS = [
    // id                 level       n  check                               required action (shown with the row)
    ['notelem',          'caution',  1, function (c) { return c.slots.length ? null : 'No telemetry received: aircraft state unknown'; },
                                                                              'Connect the drone link (WiFi or serial) and check the ground service'],
    ['link-lost',        'warning',  1, flying(linkDown),                    'Take manual RC control and land, then check the WiFi link'],
    ['link-down',        'caution',  1, grounded(linkDown),                  'Check the WiFi link and that the drone is powered before arming'],
    ['rc-lost',          'warning',  1, flying(rcLost),                      'Press Abort or Land, then check the RC transmitter is on and in range'],
    ['rc-off',           'caution',  1, grounded(rcLost),                    'Turn the RC transmitter on (ch10 kill in reach) before arming'],
    ['vbat-low',         'warning',  3, flying(vbatLow),                     'Land now and swap the pack'],
    ['vbat-low-ground',  'caution',  3, grounded(vbatLow),                   'Swap the pack before the next flight'],
    ['vbat-warn',        'caution',  3, function (c) {
      var v = c.value('status.vbat');
      return v !== null && v >= LIMITS.vbatLowV && v < LIMITS.vbatCautionV ?
        'Battery ' + v.toFixed(2) + ' V: below early-warning ' + LIMITS.vbatCautionV.toFixed(1) + ' V' : null;
    },                                                                        'Finish this run, then land and swap the pack'],
    ['estimator',        'caution',  2, function (c) {
      return c.value('status.estimator_ready') === 0 ? 'Estimator not ready (status.estimator_ready = 0)' : null;
    },                                                                        'Do not arm: wait for the estimator or reset the EKF (Estimator tab)'],
    ['stale',            'caution',  1, function (c, n) {
      var age = c.slotAge(n);
      return age !== null && age > c.ttlMs ? 'Telemetry frozen ' + fmtAge(age) + ' on slot ' + n : null;
    },                                                                        'Check the link and the slot layout: panels on this slot show frozen data'],
    ['loss',             'caution',  3, function (c, n) {
      var l = Number(c.streams[n].loss_pct || 0);
      return l > LIMITS.lossCautionPct ? 'Packet loss ' + l.toFixed(1) + '% on slot ' + n : null;
    },                                                                        'Move the ground station closer or check the antenna'],
    ['slow',             'advisory', 1, function (c, n) {
      var age = c.slotAge(n);
      return age !== null && age > LIMITS.slowMs && age <= c.ttlMs ? 'Telemetry slow (' + fmtAge(age) + ') on slot ' + n : null;
    },                                                                        ''],
    ['loss-minor',       'advisory', 3, function (c, n) {
      var l = Number(c.streams[n].loss_pct || 0);
      return l > LIMITS.lossAdvisoryPct && l <= LIMITS.lossCautionPct ? 'Packet loss ' + l.toFixed(1) + '% on slot ' + n : null;
    },                                                                        ''],
    ['armed',            'advisory', 1, function (c) { return c.value('status.arm') === 1 ? 'Drone armed' : null; }, '']
  ];
  // per-slot rules run once per telemetry slot; their id becomes '<id>-<slot>'. notelem is the start-up state, not an episode.
  var PER_SLOT = { stale: true, loss: true, slow: true, 'loss-minor': true };
  var NO_LOG = { notelem: true };
  // spoken phrase of each warning (D3); the required action follows it
  var SAY = { 'link-lost': 'Telemetry link lost', 'rc-lost': 'R C link lost', 'vbat-low': 'Battery low' };

  var RULES = RULES_ROWS.map(function (r) {
    return { id: r[0], level: r[1], n: r[2], check: r[3], action: r[4],
      perSlot: !!PER_SLOT[r[0]], log: !NO_LOG[r[0]], say: SAY[r[0]] || '' };
  });

  // a warning or caution without a required action is not an alarm
  function validate(rules) {
    var ids = {};
    rules.forEach(function (r) {
      var where = 'alarm rule ' + r.id + ': ';
      if (!r.id || ids[r.id]) throw new Error(where + 'missing or duplicate id');
      ids[r.id] = true;
      if (!LEVELS[r.level]) throw new Error(where + 'unknown level ' + r.level);
      if (LEVELS[r.level].shown && !String(r.action || '').trim()) {
        throw new Error(where + 'a ' + r.level + ' must name its required action');
      }
      if (!(r.n >= 1)) throw new Error(where + 'debounce n must be >= 1 sample');
      if (typeof r.check !== 'function') throw new Error(where + 'check is not a function');
    });
    return rules;
  }

  // ── speech (D3): warnings only, one utterance per episode, cancelled when the episode is silenced ──────────
  function browserSpeaker() {
    var synth = root.speechSynthesis, Utt = root.SpeechSynthesisUtterance;
    if (!synth || typeof synth.speak !== 'function' || typeof Utt !== 'function') return null;
    var current = null;
    return {
      speak: function (ref, text) {
        var u = new Utt(text);
        u.onend = function () { if (current === ref) current = null; };
        current = ref;
        synth.speak(u);
      },
      cancel: function (ref) {
        if (current === null || (ref !== undefined && ref !== current)) return;
        current = null;
        synth.cancel();
      }
    };
  }

  function report(where, e) {
    if (root.GSUI && root.GSUI.report) root.GSUI.report(where, e, { toast: false, once: where });
    else if (typeof console !== 'undefined' && console.warn) console.warn('[gs] ' + where + ': ' + (e && e.message));
  }

  // ── engine ────────────────────────────────────────────────────────────────────────────────────────────
  function Engine(opts) {
    opts = opts || {};
    this.rules = validate(opts.rules || RULES);
    this.logMax = opts.logMax || LOG_MAX;
    this.speaker = opts.speaker !== undefined ? opts.speaker : browserSpeaker();
    this.reset();
  }

  Engine.prototype.reset = function () {
    this._track = {};     // id -> {rule, on, off, active, text, ep}
    this._log = [];       // episodes, oldest first, at most logMax
    this._dropped = 0;
    this._seq = 0;
    this._sample = undefined;
    this._sampleMs = null;
  };

  // update(state, nowMs) -> the shown alarms (warning + caution), worst first
  Engine.prototype.update = function (state, nowMs) {
    if (nowMs === undefined || nowMs === null) nowMs = Date.now();
    if (this._sampleMs !== null && state === this._sample && nowMs - this._sampleMs < SAMPLE_MS) return this.alarms();
    this._sample = state;
    this._sampleMs = nowMs;
    var ctx = makeContext(state, nowMs);
    var hits = {};
    this.rules.forEach(function (r) {
      (r.perSlot ? ctx.slots : [null]).forEach(function (slot) {
        var text = r.check(ctx, slot);
        if (text) hits[r.perSlot ? r.id + '-' + slot : r.id] = { rule: r, text: text };
      });
    });
    var self = this;
    Object.keys(hits).forEach(function (id) {
      if (!self._track[id]) self._track[id] = { rule: hits[id].rule, on: 0, off: 0, active: false, text: '', ep: null };
    });
    Object.keys(this._track).forEach(function (id) {
      var t = self._track[id], h = hits[id];
      if (h) {
        t.on++; t.off = 0; t.text = h.text;
        if (t.active) t.ep.textEnd = h.text;
        else if (t.on >= t.rule.n) self._raise(id, t, nowMs);
        return;
      }
      t.off++; t.on = 0;
      if (!t.active) { delete self._track[id]; return; }
      if (t.off >= t.rule.n) {
        t.ep.clearedAt = nowMs;
        delete self._track[id];
      }
    });
    return this.alarms();
  };

  Engine.prototype._raise = function (id, t, nowMs) {
    var r = t.rule, lv = LEVELS[r.level];
    t.active = true;
    t.ep = { ref: ++this._seq, id: id, level: r.level, sev: lv.sev, text: t.text, textEnd: t.text, action: r.action,
      raisedAt: nowMs, clearedAt: null, ack: false, silenced: false, spoken: false };
    if (r.log) {
      this._log.push(t.ep);
      if (this._log.length > this.logMax) { this._log.shift(); this._dropped++; }
    }
    if (lv.speak && this.speaker) {
      t.ep.spoken = true;
      try {
        this.speaker.speak(t.ep.ref, 'Warning. ' + (r.say || t.text) + '. ' + r.action + '.');
      } catch (e) { report('alarm voice', e); }
    }
  };

  function view(t) {
    var lv = LEVELS[t.rule.level], ep = t.ep;
    return { id: ep.id, ref: ep.ref, level: t.rule.level, label: lv.label, status: lv.status, sev: lv.sev,
      rank: lv.rank, text: t.text, action: t.rule.action, raisedAt: ep.raisedAt, ack: ep.ack, silenced: ep.silenced };
  }

  function active(eng, shown) {
    return Object.keys(eng._track).map(function (id) { return eng._track[id]; })
      .filter(function (t) { return t.active && LEVELS[t.rule.level].shown === shown; })
      .map(view)
      .sort(function (a, b) { return (b.rank - a.rank) || (a.raisedAt - b.raisedAt); });
  }

  Engine.prototype.alarms = function () { return active(this, true); };
  Engine.prototype.advisories = function () { return active(this, false); };
  Engine.prototype.log = function () { return this._log; };
  Engine.prototype.dropped = function () { return this._dropped; };

  Engine.prototype._episode = function (ref) {
    for (var i = 0; i < this._log.length; i++) if (this._log[i].ref === ref) return this._log[i];
    var ids = Object.keys(this._track);
    for (var j = 0; j < ids.length; j++) {
      var ep = this._track[ids[j]].ep;
      if (ep && ep.ref === ref) return ep;
    }
    return null;
  };

  // operator actions, display only: acknowledge marks an episode seen, silence drops it from the banner and stops
  // its voice; neither sends, arms or gates anything and both stay in the log
  Engine.prototype.ack = function (ref) {
    var ep = this._episode(ref);
    if (ep) ep.ack = true;
    return !!ep;
  };

  Engine.prototype.silence = function (ref, on) {
    var ep = this._episode(ref);
    if (!ep) return false;
    ep.silenced = on === undefined ? !ep.silenced : !!on;
    if (ep.silenced && this.speaker) {
      try { this.speaker.cancel(ref); } catch (e) { report('alarm voice', e); }
    }
    return true;
  };

  function csvField(s) { return '"' + String(s === null || s === undefined ? '' : s).replace(/"/g, '""') + '"'; }
  function iso(ms) { return ms === null || ms === undefined ? '' : new Date(ms).toISOString(); }

  Engine.prototype.csv = function () {
    var rows = [['raised_at', 'cleared_at', 'id', 'severity', 'text_raised', 'text_last', 'acknowledged', 'silenced',
      'required_action']];
    this._log.forEach(function (ep) {
      rows.push([iso(ep.raisedAt), iso(ep.clearedAt), ep.id, ep.level, ep.text, ep.textEnd,
        ep.ack ? 'yes' : 'no', ep.silenced ? 'yes' : 'no', ep.action]);
    });
    return rows.map(function (r) { return r.map(csvField).join(','); }).join('\r\n') + '\r\n';
  };

  var _shared = null;
  function shared() { return _shared || (_shared = new Engine()); }

  root.GSAlarms = {
    version: 1,
    LEVELS: LEVELS, LIMITS: LIMITS, RULES: RULES, SAMPLE_MS: SAMPLE_MS, LOG_MAX: LOG_MAX,
    validate: validate, Engine: Engine, shared: shared, browserSpeaker: browserSpeaker, fmtAge: fmtAge
  };
})(typeof window !== 'undefined' ? window : this);
