import { chromium } from '../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs';
fs.writeFileSync('.runtime/intent-resource-browser.pid',String(process.pid));
const browser=await chromium.launch({channel:'chrome',headless:true});
try {
  const page=await browser.newPage();
  await page.goto('http://127.0.0.1:5174/core');
  for(let i=0;i<60;i++) {
    if(i>=15 && i<45) await Promise.all(Array.from({length:8},()=>page.request.get('http://127.0.0.1:8011/api/v1/health')));
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
} finally { await browser.close(); }
