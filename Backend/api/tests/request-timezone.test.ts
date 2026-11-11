import test from 'node:test';
import assert from 'node:assert/strict';
import {parseRequestTimezone} from '../src/request-timezone';

test('request timezone is optional and accepts user zones including DST regions',()=>{
 assert.equal(parseRequestTimezone(undefined),undefined);
 for(const zone of ['Asia/Seoul','America/New_York','UTC'])assert.equal(parseRequestTimezone(zone),zone);
 for(const value of [null,'',{},'Mars/Unknown',100])assert.throws(()=>parseRequestTimezone(value));
});
