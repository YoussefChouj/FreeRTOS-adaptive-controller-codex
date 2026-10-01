const fs = require('fs');
let code = fs.readFileSync('ground_station/service/tests/campaign_panel_harness.js', 'utf8');

// H4
const eCheckRegex = /if \(posts\.find.+?&&\\s+posts\.find.+?&&\\s+posts\.find.+?\)/s;
const eCheckNew = `if (posts.length === 3 &&
          posts.filter(f => f.url === '/api/campaign/pause' && JSON.parse(f.opts.body).source === 'operator').length === 1 &&
          posts.filter(f => f.url === '/api/campaign/land' && JSON.parse(f.opts.body).source === 'operator').length === 1 &&
          posts.filter(f => f.url === '/api/campaign/abort' && JSON.parse(f.opts.body).source === 'operator').length === 1)`;
code = code.replace(/if \(posts\.find.+?&&[\s\S]+?posts\.find.+?&&[\s\S]+?posts\.find.+?\)/, eCheckNew);

// H1 and H2 - this block is big
const dCheckRegex = /\/\/ d\. Pause\/Land\/Abort present[\s\S]+?ctx\._timerFn\(\);\n      setTimeout\(\(\) => \{/s;
const dCheckNew = `// d. Pause/Land/Abort present in idle, running, waiting_for_go, operator_needed and error states
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
            setTimeout(() => {`;
code = code.replace(dCheckRegex, dCheckNew);

// P1 check
const gCheckRegex = /ctx\._fakeState = {\n\s+status: 'running',[\s\S]+?if \(getEl\('cp-flights-body'\)\.innerHTML\.includes\('f1'\) && getEl\('cp-flights-body'\)\.innerHTML\.includes\('f2'\)\) passCheck\('g', 'Flights rendered'\);\n\s+else throw new Error\('Flights missing'\);/s;
const gCheckNew = `ctx._fakeState = {
                status: 'running',
                flights: [
                  { flight_id: 'f1', pack_id: 'p1', experiment: 'e1', j: 1, decision: 'go', abort_level: 'none', abort_reason: '<b>x</b>', hover_only: false },
                  { flight_id: 'f2', hover_only: true }
                ]
              };
              ctx._timerFn();
              setTimeout(() => {
                const fb = getEl('cp-flights-body').innerHTML;
                if (fb.includes('f1') && fb.includes('f2') && fb.includes('&lt;b&gt;x&lt;/b&gt;') && !fb.includes('<b>x</b>')) passCheck('g', 'Flights rendered');
                else throw new Error('Flights missing or not escaped properly');`;
code = code.replace(gCheckRegex, gCheckNew);

// H6 error text exact match
const hCheckRegex = /if \(getEl\('cp-error'\)\.style\.display === 'block' && getEl\('cp-error'\)\.textContent\.includes\('409'\)\) \{[\s\S]+?if \(getEl\('cp-error'\)\.style\.display === 'block' && getEl\('cp-error'\)\.textContent\.includes\('503'\)\) passCheck\('h', 'Errors shown'\);/s;
const hCheckNew = `if (getEl('cp-error').style.display === 'block' && getEl('cp-error').textContent === 'Conflict 409') {
              ctx._goFail = 503;
              qGoBtn.dispatch('click');
              setTimeout(() => {
                if (getEl('cp-error').style.display === 'block' && getEl('cp-error').textContent === 'Deps 503') passCheck('h', 'Errors shown');`;
code = code.replace(hCheckRegex, hCheckNew);

// H5, P4
const iCheckRegex = /if \(\!hasPost\) throw new Error\('Should POST on confirm true'\);\n\s+passCheck\('i', 'allow_agent_arm confirm'\);[\s\S]+?passCheck\('j', 'No timer after teardown'\);\n\s+console\.log\('ALL CHECKS PASSED'\);\n\s+process\.exit\(0\);\n\s+\}, 50\);\n\s+\}, 50\);/s;
const iCheckNew = `if (!hasPost) throw new Error('Should POST on confirm true');
                        
                        // P4 check: network error reverts the toggle
                        ctx._armFail = true;
                        getEl('cp-allow-arm').checked = false; // it was true
                        getEl('cp-allow-arm').dispatch('change');
                        setTimeout(() => {
                          if (!getEl('cp-allow-arm').checked) throw new Error('Toggle should revert to true on POST failure');
                          passCheck('i', 'allow_agent_arm confirm');
                          
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
                      }, 50);`;
code = code.replace(iCheckRegex, iCheckNew);

// Add missing closing brackets for nextStatus function
code = code.replace(/process\.exit\(0\);\n\s+\}, 50\);\n\s+\}, 50\);\n\s+\} else throw new Error\('Error not shown 409'\);/s,
`process.exit(0);
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
      nextStatus();`);

fs.writeFileSync('ground_station/service/tests/campaign_panel_harness.js', code);
