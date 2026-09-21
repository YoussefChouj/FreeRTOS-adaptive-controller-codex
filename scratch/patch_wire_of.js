  function wireOfBias() {
    var btnFixed = q('cp-of-fixed');
    var btnEma = q('cp-of-ema');
    var btnEkf = q('cp-of-ekf');
    var btnFreeze = q('cp-of-freeze');
    
    function setCmdAndSub(idx, val) {
      if (_els.cmdIdSelect) _els.cmdIdSelect.value = 0x1E;
      if (_els.cmdIndex) _els.cmdIndex.value = idx;
      if (_els.cmdValue) _els.cmdValue.value = val;
      updateRangeDisplay();
      submitCommand(0x1E, idx, val);
    }

    if (btnFixed) btnFixed.addEventListener('click', function() { setCmdAndSub(0, 0); });
    if (btnEma) btnEma.addEventListener('click', function() { setCmdAndSub(0, 1); });
    if (btnEkf) btnEkf.addEventListener('click', function() { setCmdAndSub(0, 2); });
    
    if (btnFreeze) btnFreeze.addEventListener('click', function() {
      // Toggle freeze: read current from state if available, else 1
      var val = 1;
      var s1 = _state && _state.streams ? (_state.streams[1] || _state.streams['1']) : null;
      if (s1 && s1.values) {
        var fVal = s1.values['slot1.g_of_bias_ema_freeze'];
        if (fVal === undefined) fVal = s1.values['g_of_bias_ema_freeze'];
        if (fVal !== undefined) val = fVal >= 0.5 ? 0 : 1;
      }
      setCmdAndSub(1, val);
    });
  }
