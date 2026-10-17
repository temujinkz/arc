import assert from 'node:assert/strict';
import { applyEvent, initialState } from '../lib/investigation.ts';

let state=applyEvent(initialState,{type:'agent.usage',cost_usd:0.01});
state=applyEvent(state,{type:'agent.usage',cost_usd:null,known_cost_usd:0.003,usage_unknown:true});
assert.ok(Math.abs(state.cost-0.013)<1e-9);
assert.equal(state.usageUnknown,true);
const stopped=applyEvent({...initialState,status:'running'},{type:'research.stopped',reason:'WAVE_LIMIT'});
assert.equal(stopped.status,'running');
assert.equal(applyEvent(stopped,{type:'investigation.complete'}).status,'complete');
console.log('Partial usage and pipeline completion checks passed.');
