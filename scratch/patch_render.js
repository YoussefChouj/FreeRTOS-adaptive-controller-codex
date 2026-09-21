  function renderReadbackValue() {
    var s1 = _state && _state.streams ? (_state.streams[1] || _state.streams['1']) : null;
    
    // OF Bias specific readback
    var ofModeSpan = q('cp-of-readback-mode');
    var ofFreezeSpan = q('cp-of-readback-freeze');
    if (ofModeSpan && ofFreezeSpan) {
      if (s1 && s1.values) {
        var mVal = s1.values['slot1.g_of_bias_mode'] !== undefined ? s1.values['slot1.g_of_bias_mode'] : s1.values['g_of_bias_mode'];
        var fVal = s1.values['slot1.g_of_bias_ema_freeze'] !== undefined ? s1.values['slot1.g_of_bias_ema_freeze'] : s1.values['g_of_bias_ema_freeze'];
        ofModeSpan.textContent = mVal !== undefined ? (mVal===0?'FIXED':mVal===1?'EMA':mVal===2?'EKF':mVal) : '\u2014';
        ofFreezeSpan.textContent = fVal !== undefined ? fVal : '\u2014';
      } else {
        ofModeSpan.textContent = '\u2014';
        ofFreezeSpan.textContent = '\u2014';
      }
    }

    if (!_els.readbackVal || !_currentSymbol) return;
    
    var rawName = _currentSymbol; 
    
    if (!s1 || !s1.values || (!(('slot1.' + _currentSymbol) in s1.values) && !(rawName in s1.values))) {
      _els.readbackVal.innerHTML = '<span style="color:var(--amber)">mapped, but not currently being read</span>';
      return;
    }

    var val = s1.values['slot1.' + _currentSymbol];
    if (val === undefined) val = s1.values[rawName];

    var ageStr = '';
    var isStale = false;
    if (s1.last_update_ns && _state.now_ns) {
      var ageS = (_state.now_ns - s1.last_update_ns) / 1e9;
      if (ageS > 2.0) {
        isStale = true;
        ageStr = ' (read ' + ageS.toFixed(1) + 's ago)';
      }
    }
    
    if (isStale) {
      _els.readbackVal.innerHTML = '<span style="color:var(--amber)">' + val + ageStr + '</span>';
    } else {
      _els.readbackVal.innerHTML = '<span style="color:var(--green);font-weight:bold;">' + val + '</span>';
    }
  }
