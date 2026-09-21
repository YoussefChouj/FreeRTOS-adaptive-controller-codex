
  function updateReadbackState() {
    if (!_els.readbackVal) return;
    var cmdId = parseInt(_els.cmdIdSelect.value, 10) || 0;
    var index = parseInt(_els.cmdIndex.value, 10) || 0;
    
    var symbol = null;
    if (_commandSymbols[cmdId] && _commandSymbols[cmdId][index]) {
      symbol = _commandSymbols[cmdId][index];
    }

    if (!symbol) {
      _currentSymbol = null;
      _els.readbackVal.innerHTML = '<span style="color:var(--muted)">no readback mapping for this command</span>';
      return;
    }

    if (_currentSymbol !== symbol) {
      _currentSymbol = symbol;
      _lastSubscribeNs = 0;
      // subscribe to slot 1 with divider 1000 for low rate
      fetch('/subscribe', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({slot: 1, divider: 100, ranges: [symbol]})
      }).catch(function(e) { console.warn('Readback subscribe err', e); });
    }

    renderReadbackValue();
  }

  function renderReadbackValue() {
    if (!_els.readbackVal || !_currentSymbol) return;
    
    var s1 = _state && _state.streams ? (_state.streams[1] || _state.streams['1']) : null;
    if (!s1 || !s1.values || !(('slot1.' + _currentSymbol) in s1.values)) {
      _els.readbackVal.innerHTML = '<span style="color:var(--amber)">mapped, but not currently being read</span>';
      return;
    }

    var val = s1.values['slot1.' + _currentSymbol];
    var t_ms = s1.values['slot1.t_ms'];
    var ageStr = '';
    
    if (t_ms != null && _state.now_ns) {
      // t_ms is FC clock. It's difficult to compare directly to host now_ns, 
      // but hub calculates age_ns for slots in slot_status. Wait, _slot_status does that.
      // Let's just use s1.last_update_ns if available.
    }
    
    // We can compute age using s1.last_update_ns
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
