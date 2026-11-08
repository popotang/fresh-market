import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const RATE_LIMIT_RE = /(?:您的)?操作(?:过于)?频繁|请求(?:过于)?频繁|太频繁|请?稍后再试|too many requests|rate[\s_-]*limit|\b429\b/i;

function gateScript() {
  if (process.env.KEMAI_SOURCE_GATE_SCRIPT) return process.env.KEMAI_SOURCE_GATE_SCRIPT;
  const directory = process.env.KEMAI_SOURCE_GATE_DIR || join(homedir(), '.opencli', 'kemai-source-gate');
  let config;
  try {
    config = JSON.parse(readFileSync(join(directory, 'config.json'), 'utf8'));
  } catch (error) {
    throw new Error(`科迈请求协调器未配置：${error.message}；先执行 python3 kemai_source_gate.py configure`);
  }
  if (!config.script_path) throw new Error('科迈请求协调器配置缺少 script_path');
  return config.script_path;
}

function openLease(label) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.env.KEMAI_SOURCE_GATE_PYTHON || 'python3',
      [gateScript(), 'lease', label], { stdio: ['pipe', 'pipe', 'pipe'] });
    let ready = false;
    let stdout = '';
    let stderr = '';
    let settled = false;
    let exitResolve;
    const exited = new Promise(resolveExit => { exitResolve = resolveExit; });
    child.stdout.on('data', chunk => {
      stdout += chunk.toString();
      if (!ready && stdout.includes('READY\n')) {
        ready = true;
        settled = true;
        resolve(async rateLimitMessage => {
          if (rateLimitMessage) child.stdin.write(`RATE_LIMIT ${JSON.stringify(rateLimitMessage)}\n`);
          child.stdin.end();
          const code = await exited;
          if (code !== 0) throw new Error(`科迈请求协调器退出 ${code}：${stderr.slice(-500)}`);
        });
      }
    });
    child.stderr.on('data', chunk => { stderr += chunk.toString(); });
    child.on('error', error => {
      if (!settled) { settled = true; reject(error); }
      exitResolve(1);
    });
    child.on('close', code => {
      if (!settled) { settled = true; reject(new Error(`科迈请求协调器未就绪：${stderr.slice(-500)}`)); }
      exitResolve(code);
    });
  });
}

export async function withSourceLease(label, operation) {
  const release = await openLease(label);
  let failure;
  try {
    return await operation();
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    const signal = failure && (failure.code === 'RATE_LIMIT' || RATE_LIMIT_RE.test(String(failure)))
      ? String(failure.message || failure).slice(-500) : null;
    try {
      await release(signal);
    } catch (error) {
      if (!failure) throw error;
    }
  }
}
