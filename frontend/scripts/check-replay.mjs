// Offline browser acceptance. Requires an isolated Chrome with --remote-debugging-port=9223.
import { readFile, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const file = process.argv[2];
if (!file) throw new Error('Pass the generated replay.json path');
const bundle = JSON.parse(await readFile(file, 'utf8'));
const pages = await (await fetch('http://127.0.0.1:9223/json')).json();
const page = pages.find(p => p.type === 'page');
const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let serial = 0; const pending = new Map(), errors = [], blocked = [];
const command = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++serial; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params }));
});
socket.onmessage = ({ data }) => {
  const message = JSON.parse(data);
  if (message.id) {
    const task = pending.get(message.id); pending.delete(message.id);
    if (message.error) task.reject(message.error); else task.resolve(message.result);
  } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
  else if (message.method === 'Fetch.requestPaused') {
    const {requestId, request} = message.params;
    const url = new URL(request.url);
    if (url.hostname === '127.0.0.1' && url.port === '3000' && !url.pathname.startsWith('/api/')) {
      command('Fetch.continueRequest', {requestId});
    } else {
      blocked.push(request.url);
      command('Fetch.fulfillRequest', {requestId, responseCode: 403, body: Buffer.from('Offline check blocks research requests').toString('base64')});
    }
  }
};
const evaluate = async expression => {
  const result = await command('Runtime.evaluate', {expression, returnByValue:true, awaitPromise:true});
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async expression => {
  for (let i=0; i<120; i++) { if (await evaluate(expression)) return; await sleep(250); }
  throw new Error('Timed out: '+expression);
};
try {
  await command('Page.enable'); await command('Runtime.enable');
  await command('Fetch.enable', {patterns:[{urlPattern:'*'}]});
  await command('Emulation.setDeviceMetricsOverride', {width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await command('Page.navigate', {url:'http://127.0.0.1:3000/research/replay'});
  await until(`!!document.querySelector('input[type=file]') && Object.keys(document.querySelector('input[type=file]')).some(key => key.startsWith('__reactProps$'))`);
  // Replay every event twice to check duplicate delivery, using the same local file format.
  const duplicateBundle={...bundle,events:bundle.events.flatMap(event=>[event,event])};
  await evaluate(`(()=>{const input=document.querySelector('input[type=file]');const transfer=new DataTransfer();transfer.items.add(new File([${JSON.stringify(JSON.stringify(duplicateBundle))}], 'replay.json',{type:'application/json'}));input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await until(`document.body.innerText.includes('Wave 3 of 3')`);
  assert.equal(await evaluate(`document.querySelectorAll('[aria-label="Research task ledger"] > li').length`),8);
  assert.equal(await evaluate(`document.body.innerText.includes('Stopped: wave limit')`),true);
  assert.equal(await evaluate(`document.querySelectorAll('[aria-label="Claim coverage"] > div').length`),3);
  await evaluate(`document.querySelector('[aria-label="Recursive research progress"] details').open=true`);
  await until(`!!document.querySelector('canvas')`);
  assert.equal(await evaluate(`document.body.innerText.includes('Simulated outage')`),true);
  assert.equal(await evaluate(`document.body.innerText.includes('Uncommitted preview')`),true);
  assert.equal(await evaluate(`document.documentElement.scrollWidth > innerWidth`),false);
  await writeFile('/private/tmp/arc-recursive-desktop.png', Buffer.from((await command('Page.captureScreenshot',{format:'png'})).data,'base64'));
  await command('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await sleep(300);
  assert.equal(await evaluate(`document.documentElement.scrollWidth > innerWidth`),false);
  await writeFile('/private/tmp/arc-recursive-mobile.png', Buffer.from((await command('Page.captureScreenshot',{format:'png'})).data,'base64'));
  assert.deepEqual(errors,[]); assert.deepEqual(blocked,[]);
  console.log(JSON.stringify({tasks:8,claims:3,duplicate_events_ignored:true,mobile_overflow:false,browser_errors:errors,unexpected_requests:blocked}));
} finally {
  await command('Fetch.disable'); socket.close();
}
