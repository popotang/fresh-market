import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {sha256} from '../../kemai_pipeline/js/sha256.mjs';
test('portable digest matches native SHA-256 across Unicode and padding boundaries',()=>{
 const inputs=['','abc','商品😀\n',...Array.from({length:70},(_,i)=>'x'.repeat(i)),'汉'.repeat(16000),'x'.repeat(64000)];
 for(const input of inputs)assert.equal(sha256(input),createHash('sha256').update(input).digest('hex'));
});
