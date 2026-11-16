"""Demo-only metadata with atomic Redis admission/state transitions (not publishing)."""
import json
import os
import re
from uuid import UUID

from src.identifiers import validate_identifier

PREFIX = 'demo:v1:'
ACTIVE = PREFIX+'uploads:active'
COUNTS = PREFIX+'uploads:counts'

ADMIT = '''
local prior = redis.call('GET', KEYS[1])
if prior then
  local item = cjson.decode(prior)
  if item.digest ~= ARGV[1] then return {'conflict'} end
  local job = redis.call('GET', 'demo:v1:job:' .. item.job_id)
  if not job then return {'broken'} end
  return {'existing', job}
end
if redis.call('EXISTS', KEYS[2]) == 1 or redis.call('EXISTS', KEYS[3]) == 1 then return {'collision'} end
if redis.call('SCARD', KEYS[4]) >= 2 then return {'capacity'} end
if tonumber(redis.call('HGET', KEYS[5], 'global') or '0') >= 20 then return {'quota'} end
if tonumber(redis.call('HGET', KEYS[5], ARGV[2]) or '0') >= 10 then return {'quota'} end
redis.call('SET', KEYS[1], cjson.encode({digest=ARGV[1], job_id=ARGV[3]}))
redis.call('SET', KEYS[2], ARGV[4])
redis.call('SET', KEYS[3], ARGV[4])
redis.call('SADD', KEYS[4], ARGV[3])
redis.call('HINCRBY', KEYS[5], 'global', 1)
redis.call('HINCRBY', KEYS[5], ARGV[2], 1)
return {'created', ARGV[4]}
'''
CHECK = '''
if redis.call('SCARD', KEYS[1]) >= 2 then return 'capacity' end
if tonumber(redis.call('HGET', KEYS[2], 'global') or '0') >= 20 then return 'quota' end
if tonumber(redis.call('HGET', KEYS[2], ARGV[1]) or '0') >= 10 then return 'quota' end
return 'available'
'''
TRANSITION = '''
local raw = redis.call('GET', KEYS[1])
if not raw then return {'missing'} end
local job = cjson.decode(raw)
if job.document_id ~= ARGV[3] or job.tenant_id ~= ARGV[4] or job.job_id ~= ARGV[5] then return {'broken'} end
local document_raw = redis.call('GET', KEYS[3])
if not document_raw then return {'broken'} end
local document = cjson.decode(document_raw)
if document.document_id ~= job.document_id or document.job_id ~= job.job_id or document.tenant_id ~= job.tenant_id then return {'broken'} end
local action = ARGV[1]
if action == 'claim' then
  if job.status ~= 'queued' and job.status ~= 'submission_unknown' and job.status ~= 'retrying' then return {'stale', raw} end
  if tonumber(job.attempt) + 1 ~= tonumber(ARGV[2]) then return {'stale', raw} end
  job.status = 'processing'; job.attempt = tonumber(ARGV[2])
elseif action == 'dispatched' then
  if job.status == 'submission_unknown' and tonumber(job.attempt) == -1 then job.status = 'queued' end
elseif action == 'publish_failed' then
  if job.status ~= 'submission_unknown' or tonumber(job.attempt) ~= -1 then return {'stale', raw} end
  job.status = 'failed'
else
  if job.status ~= 'processing' or tonumber(job.attempt) ~= tonumber(ARGV[2]) then return {'stale', raw} end
  job.status = action
end
local encoded = cjson.encode(job)
redis.call('SET', KEYS[1], encoded)
redis.call('SET', KEYS[3], encoded)
if job.status == 'completed' or job.status == 'failed' then redis.call('SREM', KEYS[2], job.job_id) end
return {'updated', encoded}
'''


def canonical_uuid(value):
    if not isinstance(value, str) or len(value) != 36: raise ValueError('Invalid UUID')
    result = str(UUID(value))
    if result != value.lower(): raise ValueError('Invalid UUID')
    return result


def validate_record(record):
    if not isinstance(record, dict): raise ValueError('Invalid demo metadata')
    validate_identifier(record['tenant_id']); validate_identifier(record['document_id'])
    if record['tenant_id'] not in ('acme','globex'): raise ValueError('Invalid demo metadata')
    if not record['document_id'].startswith('doc-') or record['document_id'][4:] != canonical_uuid(record['document_id'][4:]):
        raise ValueError('Invalid demo metadata')
    from src.demo.upload_files import safe_filename
    safe_filename(record['filename'])
    if not isinstance(record['digest'], str) or re.fullmatch('[0-9a-f]{64}', record['digest']) is None:
        raise ValueError('Invalid demo metadata')
    for field, maximum in (('size_bytes',5242880),('page_count',10),('chunk_count',200),('text_characters',100000)):
        if type(record[field]) is not int or not 1 <= record[field] <= maximum: raise ValueError('Invalid demo metadata')
    if type(record['attempt']) is not int or not -1 <= record['attempt'] <= 3: raise ValueError('Invalid demo metadata')
    if canonical_uuid(record['job_id']) != record['job_id']: raise ValueError('Invalid demo metadata')
    if record['status'] not in ('submission_unknown','queued','processing','retrying','completed','failed'):
        raise ValueError('Invalid demo metadata')
    return record


class UploadStore:
    def __init__(self, client): self.client = client

    @classmethod
    def from_demo_env(cls):
        from src.demo.config import ACTIVE_SETTINGS
        if ACTIVE_SETTINGS is None or os.getenv('DEMO_ENABLED') != '1' or os.getenv('REDIS_JOBS_URL') != 'redis://redis:6379/1':
            raise ValueError('Invalid demo metadata configuration')
        import redis
        from redis.retry import Retry
        from redis.backoff import NoBackoff
        client = redis.Redis.from_url('redis://redis:6379/1', decode_responses=True,
            socket_timeout=3, socket_connect_timeout=2, retry=Retry(NoBackoff(), 0))
        return cls(client)

    def lookup(self, tenant_id, key, digest):
        validate_identifier(tenant_id); key = canonical_uuid(key)
        prior = self.client.get(PREFIX+'submission:'+tenant_id+':'+key)
        if prior is None: return None
        item = json.loads(prior)
        if item['digest'] != digest: return 'conflict'
        result = self.get(item['job_id'])
        if result is None or result['tenant_id'] != tenant_id or result['digest'] != digest:
            raise ValueError('Invalid demo metadata')
        return result

    def capacity(self, tenant_id):
        validate_identifier(tenant_id)
        return self.client.eval(CHECK, 2, ACTIVE, COUNTS, tenant_id)

    def admit(self, record, key):
        validate_record(record); key = canonical_uuid(key)
        result = self.client.eval(ADMIT, 5,
            PREFIX+'submission:'+record['tenant_id']+':'+key,
            PREFIX+'job:'+record['job_id'], PREFIX+'document:'+record['document_id'], ACTIVE, COUNTS,
            record['digest'], record['tenant_id'], record['job_id'], json.dumps(record))
        if result[0] in ('created','existing'):
            accepted = validate_record(json.loads(result[1]))
            if accepted['tenant_id'] != record['tenant_id'] or accepted['digest'] != record['digest']:
                raise ValueError('Invalid demo metadata')
            return result[0], accepted
        return result[0], None

    def get(self, job_id, tenant_id=None):
        job_id = canonical_uuid(job_id)
        raw = self.client.get(PREFIX+'job:'+job_id)
        if raw is None: return None
        record = json.loads(raw)
        if tenant_id is not None and (not isinstance(record, dict) or record.get('tenant_id') != tenant_id):
            return None
        record = validate_record(record)
        if record['job_id'] != job_id: raise ValueError('Invalid demo metadata')
        return record

    def transition(self, job_id, action, attempt=-1):
        if action not in ('claim','dispatched','publish_failed','retrying','failed','completed'):
            raise ValueError('Invalid job transition')
        if type(attempt) is not int or not -1 <= attempt <= 3: raise ValueError('Invalid attempt')
        record = self.get(job_id)
        if record is None: return 'missing', None
        result = self.client.eval(TRANSITION, 3, PREFIX+'job:'+job_id, ACTIVE,
            PREFIX+'document:'+record['document_id'], action, attempt,
            record['document_id'], record['tenant_id'], record['job_id'])
        if result[0] not in ('updated','stale'): raise ValueError('Invalid demo metadata')
        return result[0], validate_record(json.loads(result[1]))

    def close(self): self.client.close()
