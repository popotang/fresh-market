import {readFileSync} from 'node:fs';
import {cli} from '@jackwener/opencli/registry';

// Local, static metadata only. Do not import the browser bridge or source gate here.
const manifest = JSON.parse(readFileSync(new URL('./manifest.json', import.meta.url), 'utf8'));

cli({
  site: 'kemai',
  name: 'capabilities',
  access: 'read',
  description: '返回科迈适配器静态命令、输出和副作用契约；不访问浏览器或科迈源站',
  browser: false,
  navigateBefore: false,
  args: [],
  columns: ['schema_version', 'adapter', 'adapter_version', 'command_count', 'source_requests', 'authentication', 'source_acceptance'],
  func: () => [{
    ...manifest,
    command_count: manifest.commands.length,
    source_requests: 0,
    authentication: null,
    source_acceptance: 'not_checked',
  }],
});
