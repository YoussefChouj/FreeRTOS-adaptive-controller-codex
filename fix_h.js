const fs = require('fs');
let code = fs.readFileSync('ground_station/service/tests/campaign_panel_harness.js', 'utf8');

// H1 fix: d check async
const h1Old = `      // d. Pause/Land/Abort present in idle, running, waiting_for_go, operator_needed and error states
      const statuses = ['idle', 'running', 'waiting_for_go', 'operator_needed', 'error'];
      let dPass = true;
      statuses.forEach(s => {
        ctx._fakeState = { status: s };
        ctx._timerFn(); // poll
        if (!getEl('cp-pause-btn') || !getEl('cp-land-btn') || !getEl('cp-abort-btn')) dPass = false;
        if (getEl('cp-pause-btn').style.display === 'none') dPass = false; // Never hidden
      });
      if (dPass) passCheck('d', 'Buttons present');
      else throw new Error('Buttons not present');
      
      // f. waiting_for_go shows the pack and prefills the pack ID
      qPack.dataset.touched = '';
      ctx._fakeState = { status: 'waiting_for_go', waiting_pack: 'wp789' };
      ctx._timerFn();
      setTimeout(() => {`;

const h1New = `      // d. Pause/Land/Abort present in idle, running, waiting_for_go, operator_needed and error states
      const statuses = ['idle', 'running', 'waiting_for_go', 'operator_needed', 'error'];
      let dIndex = 0;
      function nextStatus() {
        if (dIndex >= statuses.length) {
          passCheck('d', 'Buttons present');
          
          // H2 fix for f: clear touched via 'idle' state instead of direct dataset manipulation
          ctx._fakeState = { status: 'idle' };
          ctx._timerFn();
          setTimeout(() => {
            ctx._fakeState = { status: 'waiting_for_go', waiting_pack: 'wp789' };
            ctx._timerFn();
            setTimeout(() => {
              if (qPack.value === 'wp789' && getEl('cp-wait-msg').textContent.includes('wp789')) passCheck('f', 'Waiting prefilled');
              else throw new Error('Waiting prefill failed');
              
              // g. flights render one row each, P1 check: HTML escaping
              ctx._fakeState = {
                status: 'running',
                flights: [
                  { flight_id: 'f1', pack_id: 'p1', experiment: 'e1', j: 1, decision: 'go', abort_level: 'none', abort_reason: '<b>x</b>', hover_only: false },
                  { flight_id: 'f2', hover_only: true }
                ]
              };
              ctx._timerFn();
              setTimeout(() => {
                const fb = getEl('cp-flights-body').innerHTML;
                if (fb.includes('f1') && fb.includes('f2') && fb.includes('&lt;b&gt;x&lt;/b&gt;') && !fb.includes('<b>x</b>')) passCheck('g', 'Flights rendered and escaped');
                else throw new Error('Flights missing or not escaped properly');
                
                // h. 409 and 503 error text shown
                qPath.value = 'a'; qPack.value = 'b';
                checkIds.forEach(id => getEl('cp-chk-' + id).checked = true);
                qPath.dispatch('input'); // re-enable go
                ctx._goFail = 409;
                qGoBtn.dispatch('click');
                setTimeout(() => {
                  if (getEl('cp-error').style.display === 'block' && getEl('cp-error').textContent === 'Conflict 409') {
                    ctx._goFail = 503;
                    qGoBtn.dispatch('click');
                    setTimeout(() => {
                      if (getEl('cp-error').style.display === 'block' && getEl('cp-error').textContent === 'Deps 503') passCheck('h', 'Errors shown');
                      else throw new Error('Error not shown 503');
                      
                      // i. allow_agent_arm
                      fetchCalls = [];
                      confirmResult = false;
                      const armToggle = getEl('cp-allow-arm');
                      armToggle.checked = true;
                      armToggle.dispatch('change');
                      if (fetchCalls.some(f => f.url === '/api/agent/control' && f.opts && f.opts.method === 'POST')) throw new Error('Should not POST on confirm false');
                      
                      confirmResult = true;
                      armToggle.checked = true;
                      armToggle.dispatch('change');
                      setTimeout(() => {
                        if (!fetchCalls.some(f => f.url === '/api/agent/control' && f.opts && f.opts.method === 'POST' && JSON.parse(f.opts.body).allow_agent_arm === true)) throw new Error('Should POST on confirm true');
                        
                        // P4 check: network error reverts the toggle
                        ctx._armFail = true;
                        armToggle.checked = false; // it was true
                        armToggle.dispatch('change');
                        setTimeout(() => {
                          if (!armToggle.checked) throw new Error('Toggle should revert to true on POST failure');
                          passCheck('i', 'allow_agent_arm confirm and revert');
                          
                          if (api.submitCount === 0 && api.gatedCount === 0) passCheck('k', 'Zero old API calls');
                          else throw new Error('Called old API');
                          
                          // j check
                          const capturedTimer = ctx._timerFn;
                          ctx.window.__PLUGIN_DESTROY__();
                          fetchCalls = [];
                          if (ctx._timerFn !== null) throw new Error('Timer not cleared');
                          
                          if (capturedTimer) {
                            try { capturedTimer(); } catch(e) {}
                          }
                          setTimeout(() => {
                            if (fetchCalls.length !== 0) throw new Error('Fetched after teardown');
                            passCheck('j', 'No timer after teardown');
                            
                            console.log('ALL CHECKS PASSED');
                            process.exit(0);
                          }, 50);
                        }, 50);
                      }, 50);
                    }, 50);
                  } else throw new Error('Error not shown 409');
                }, 50);
              }, 50);
            }, 50);
          }, 50);
        } else {
          ctx._fakeState = { status: statuses[dIndex++] };
          ctx._timerFn();
          setTimeout(() => {
            if (!getEl('cp-pause-btn') || !getEl('cp-land-btn') || !getEl('cp-abort-btn')) throw new Error('Buttons not present');
            if (getEl('cp-pause-btn').style.display === 'none') throw new Error('Buttons not present');
            nextStatus();
          }, 10);
        }
      }
      nextStatus();`;

// Use replace carefully, splitting at dPass and the end of runHarness
let parts = code.split(h1Old.split('\n')[0]);
let endParts = code.split(/ctx._fakeState = { status: 'waiting_for_go', waiting_pack: 'wp789' };[\s\S]+?passCheck\('f', 'Waiting prefilled'\);/);

// Wait, the nested structure is complex, let's just do a big regex replacement or manual replace of the whole bottom half of runHarness.
