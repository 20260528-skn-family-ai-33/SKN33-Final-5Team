import {GetObjectCommand, PutObjectCommand, S3Client} from '@aws-sdk/client-s3';
import {createHash} from 'node:crypto';
import {relative, sep} from 'node:path';
import {baseDataDirectory, dataDirectory} from './paths';

const bucket = process.env.A4B_S3_BUCKET?.trim();
const region = process.env.AWS_REGION?.trim();
const client = bucket ? new S3Client({region, maxAttempts: 3}) : undefined;
const workspacePattern = /^users\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
const filePattern = /^(?:builder-originals\/[a-f0-9]{64}\.[a-z0-9]+|(?:uploads|results)\/[a-f0-9]{64})$/;

export function validateWorkspaceFiles(authenticated: boolean) {
  if (bucket && (!authenticated || !region)) {
    throw new Error('S3 파일 저장에는 로그인과 AWS_REGION 설정이 필요합니다.');
  }
}

export function binaryObjectPath(kind: 'uploads' | 'results', bytes: Buffer) {
  return kind + '/' + createHash('sha256').update(bytes).digest('hex');
}

function objectKey(path: string) {
  const workspace = relative(baseDataDirectory(), dataDirectory()).split(sep).join('/');
  if (!workspacePattern.test(workspace) || !filePattern.test(path)) {
    throw new Error('작업 공간 파일 경로를 확인하세요.');
  }
  return 'app/v1/' + workspace + '/' + path;
}

export async function saveWorkspaceFile(path: string, bytes: Buffer) {
  if (!client) return;
  const Key = objectKey(path);
  try {
    await client.send(new PutObjectCommand({Bucket: bucket, Key, Body: bytes}), {
      abortSignal: AbortSignal.timeout(20000),
    });
  } catch {
    throw new Error('S3에 파일을 저장하지 못했습니다. 잠시 후 다시 시도하세요.');
  }
}

export async function readWorkspaceFile(path: string, readLegacy: () => Promise<Buffer> | Buffer) {
  if (!client) return readLegacy();
  const Key = objectKey(path);
  try {
    const {Body} = await client.send(new GetObjectCommand({Bucket: bucket, Key}), {
      abortSignal: AbortSignal.timeout(20000),
    });
    if (!Body) throw new Error('Empty S3 response');
    return Buffer.from(await Body.transformToByteArray());
  } catch (error) {
    if (!(error instanceof Error) || error.name !== 'NoSuchKey') {
      throw new Error('S3에서 파일을 읽지 못했습니다. 잠시 후 다시 시도하세요.');
    }
  }
  const bytes = await readLegacy();
  await saveWorkspaceFile(path, bytes);
  return bytes;
}

export const workspaceFilesEnabled = Boolean(client);
