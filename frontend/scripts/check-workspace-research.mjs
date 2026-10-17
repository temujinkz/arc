// Exercises real FastAPI/SSE in the normal workspace. Never permits fresh research.
// Requires localhost:3000, localhost:8000 and an isolated Chrome CDP on :9223.
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
const cachedId = '543e4074-d81b-4ec5-993d-d23c36794c83';
const existingSession = process.argv[2];
const pages = await (await fetch('http://127.0.0.1:9223/json')).json();
const socket = new WebSocket(pages.find(p => p.type === 'page').webSocketDebuggerUrl);
await new Promise((resolve,reject) => { socket.onopen=resolve; socket.onerror=reject; });
let serial=0; const pending=new Map(), errors=[], blocked=[], starts=[];
const command=(method,params={}) => new Promise((resolve,reject)=>{
  const id=++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));
});
socket.onmessage=({data})=>{
  const message=JSON.parse(data);
  if(message.id){const task=pending.get(message.id);pending.delete(message.id);if(message.error)task.reject(message.error);else task.resolve(message.result);}
  else if(message.method==='Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
  else if(message.method==='Fetch.requestPaused'){
    const {requestId,request}=message.params; const url=new URL(request.url);
    const local=['localhost','127.0.0.1'].includes(url.hostname);
    let allowed=local && url.port==='3000' && request.method==='GET';
    if(local && url.port==='8000'){
      allowed=['GET','OPTIONS'].includes(request.method) && (
        ['/api/investigations','/api/research/cached-profile'].includes(url.pathname) ||
        /^\/api\/investigate\/stream\/[a-zA-Z0-9_-]+$/.test(url.pathname) ||
        /^\/api\/investigate\/[a-zA-Z0-9_-]+\/(spans|evidence)\/[a-zA-Z0-9_-]+$/.test(url.pathname));
      if(request.method==='POST' && url.pathname==='/api/investigations'){
        const body=JSON.parse(request.postData || '{}');
        allowed=body.cached_source_session_id===cachedId && body.refresh_sources===false && body.target==='Pawan kumar';
        if(allowed)starts.push(body.request_id);
      }
    }
    if(allowed)command('Fetch.continueRequest',{requestId});
    else {blocked.push(`${request.method} ${request.url}`);command('Fetch.fulfillRequest',{requestId,responseCode:403,body:Buffer.from('Browser check blocks non-cached operations').toString('base64')});}
  }
};
const evaluate=async expression=>{
  const result=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if(result.exceptionDetails)throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const until=async expression=>{for(let i=0;i<160;i++){if(await evaluate(expression))return;await sleep(250);}throw new Error('Timed out: '+expression);};
const clickText=async (text,selector='button')=>evaluate(`(()=>{const b=[...document.querySelectorAll(${JSON.stringify(selector)})].find(e=>e.getClientRects().length && e.innerText.trim()===${JSON.stringify(text)});if(!b)throw Error('Missing button '+${JSON.stringify(text)});b.click();})()`);
const screenshot=async name=>writeFile(`/private/tmp/arc-workspace-${name}.png`,Buffer.from((await command('Page.captureScreenshot',{format:'png'})).data,'base64'));
try{
  await command('Page.enable');await command('Runtime.enable');
  await command('Fetch.enable',{patterns:[{urlPattern:'*'}]});
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  const reset=await command('Page.addScriptToEvaluateOnNewDocument',{source:existingSession ? `sessionStorage.setItem('arc.current-investigation',${JSON.stringify(existingSession)})` : "sessionStorage.removeItem('arc.current-investigation')"});
  await command('Page.navigate',{url:'http://127.0.0.1:3000'});
  await until(`document.body.innerText.includes('Review saved Pawan kumar evidence')`);
  await command('Page.removeScriptToEvaluateOnNewDocument',{identifier:reset.identifier});
  if(!existingSession)await clickText('Review saved Pawan kumar evidence · no credits');
  await until(`document.body.innerText.includes('Wave 3 of 3') && document.body.innerText.includes('Complete')`);
  const sessionId=await evaluate(`sessionStorage.getItem('arc.current-investigation')`);
  assert.equal(starts.length,existingSession ? 0 : 1);
  assert.equal(await evaluate(`document.querySelectorAll('[aria-label="Research task ledger"] > li').length`),8);
  await screenshot('conversation');
  await clickText('Dossier','nav button');
  await until(`document.querySelector('section[aria-label="Dossier"]').innerText.includes('Open Agent Network')`);
  assert.equal(await evaluate(`document.querySelector('section[aria-label="Dossier"]').innerText.includes('Stela')`),true);
  assert.equal(await evaluate(`document.querySelector('section[aria-label="Dossier"]').innerText.includes('Flightdeck')`),true);
  assert.equal(await evaluate(`document.querySelector('section[aria-label="Dossier"]').innerText.includes('INSUFFICIENT EVIDENCE')`),true);
  await screenshot('dossier');
  await evaluate(`(()=>{const b=[...document.querySelectorAll('section[aria-label="Dossier"] button')].find(e=>e.innerText.includes('40%'));if(!b)throw Error('Missing Stela claim');b.click();})()`);
  await until(`!!document.querySelector('[aria-label="Exact source passages"] blockquote')`);
  const inspector=await evaluate(`document.querySelector('[aria-label="Exact source passages"]').innerText`);
  assert.match(inspector,/40%/);assert.match(inspector,/SELF_REPORTED_ONLY/);assert.match(inspector,/JSON pointer/);assert.match(inspector,/2026-10/);
  await screenshot('stela-evidence');
  await evaluate(`document.querySelector('[aria-label="Close evidence"]').click()`);
  await clickText('Inspect exact passages','section[aria-label="Dossier"] button');
  await until(`!!document.querySelector('[aria-label="Exact source passages"] blockquote')`);
  await evaluate(`document.querySelector('[aria-label="Close evidence"]').click()`);
  await clickText('Graph','nav button');
  await until(`!!document.querySelector('section[aria-label="Graph"] canvas')`);
  await screenshot('graph');
  await evaluate(`(()=>{const select=document.querySelector('#node-picker');select.value=[...select.options].find(o=>o.text.startsWith('Claim: Open Agent Network')).value;select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await until(`!!document.querySelector('[aria-label="Exact source passages"] blockquote')`);
  assert.match(await evaluate(`document.querySelector('[aria-label="Exact source passages"]').innerText`),/SELF_REPORTED_ONLY/);
  await screenshot('graph-evidence');
  await evaluate(`document.querySelector('[aria-label="Close evidence"]').click()`);
  // Reload reuses the saved investigation; it must not POST another run.
  await command('Page.reload');
  await until(`document.body.innerText.includes('Wave 3 of 3') && document.body.innerText.includes('Complete')`);
  assert.equal(await evaluate(`sessionStorage.getItem('arc.current-investigation')`),sessionId);
  assert.equal(starts.length,existingSession ? 0 : 1);
  await clickText('Dossier','nav button');
  await command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await sleep(300);
  assert.equal(await evaluate(`document.documentElement.scrollWidth > innerWidth`),false);
  await screenshot('mobile');
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);
  const report={session_id:sessionId,tasks:8,projects:['Stela','Flightdeck','Open Agent Network'],exact_passages:true,cited_portrait:true,missing_citation_withheld:true,reload_without_new_run:true,mobile_overflow:false,errors,blocked,paid_calls:0};
  await writeFile('/private/tmp/arc-workspace-browser.json',JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
}catch(error){
  console.error(JSON.stringify({error:String(error),errors,blocked,starts}));
  await writeFile('/private/tmp/arc-workspace-browser-failure.txt',await evaluate('document.body.innerText'));
  await screenshot('failure');throw error;
}finally{await command('Fetch.disable');socket.close();}
