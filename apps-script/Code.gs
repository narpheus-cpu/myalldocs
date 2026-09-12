/**
 * Personal Book Knowledge Base relay.
 * Script Properties required: GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO,
 * CALLBACK_SECRET, COMPLETION_EMAIL, DRIVE_ROOT_FOLDER_ID.
 */
function doGet() {
  return json_({ok: true, service: 'book-indexer-relay'});
}

function doPost(e) {
  try {
    var body = JSON.parse((e.postData && e.postData.contents) || '{}');
    if (body.route === 'callback' || body.event === 'indexing-complete') return handleCallback_(body);
    if (body.route === 'progress') return handleProgress_(body);
    if (body.route === 'runtime-key') return handleRuntimeKey_(body);
    assertAuthorizedUser_(body);
    if (body.route === 'status') return json_({ok: true, progress: liveStatus_(), geminiKey: geminiKeyStatus_()});
    if (body.route === 'update-api-key') return json_(updateApiKey_(body));
    assertFolderWithinRoot_(body.folderId);
    if (body.route === 'preview') return json_(previewFolder_(body.folderId, body.recursive !== false));
    if (body.route === 'dispatch') return json_(dispatchWorkflow_(body));
    throw new Error('Unknown route');
  } catch (error) {
    return json_({ok: false, error: String(error && error.message || error)});
  }
}

function assertAuthorizedUser_(body) {
  var expected = PropertiesService.getScriptProperties().getProperty('AUTHORIZED_EMAIL') || 'narepheus@gmail.com';
  var actual = '';
  var token = String((body && body.accessToken) || '');
  if (token) {
    var response = UrlFetchApp.fetch('https://www.googleapis.com/oauth2/v3/userinfo', {
      headers: {Authorization: 'Bearer ' + token}, muteHttpExceptions: true
    });
    if (response.getResponseCode() === 200) {
      actual = String(JSON.parse(response.getContentText() || '{}').email || '');
    }
  } else {
    actual = Session.getActiveUser().getEmail();
  }
  if (!actual || actual.toLowerCase() !== expected.toLowerCase()) {
    throw new Error('허용된 Google 계정으로 로그인한 경우에만 실행할 수 있습니다.');
  }
}

function assertCallbackSecret_(body) {
  if (String(body.callbackSecret || '') !== requiredProperty_('CALLBACK_SECRET')) {
    throw new Error('Invalid callback secret');
  }
}

function updateApiKey_(body) {
  var key = String(body.geminiApiKey || '').trim();
  if (!/^[A-Za-z0-9_-]{20,200}$/.test(key)) throw new Error('Gemini API Key 형식이 올바르지 않습니다.');
  PropertiesService.getScriptProperties().setProperties({
    GEMINI_API_KEY: key, GEMINI_KEY_UPDATED_AT: new Date().toISOString()
  });
  return {ok: true, geminiKey: geminiKeyStatus_()};
}

function geminiKeyStatus_() {
  var properties = PropertiesService.getScriptProperties();
  var key = properties.getProperty('GEMINI_API_KEY') || '';
  return {configured: Boolean(key), masked: key ? '••••' + key.slice(-4) : '', updatedAt: properties.getProperty('GEMINI_KEY_UPDATED_AT') || ''};
}

function handleRuntimeKey_(body) {
  assertCallbackSecret_(body);
  var key = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY') || '';
  return json_({ok: true, geminiApiKey: key});
}

function handleProgress_(body) {
  assertCallbackSecret_(body);
  var source = body.progress || {};
  var names = ['status','phase','message','model','currentFileName','currentFileIndex','totalFiles',
    'currentChunk','totalChunks','complete','skipped','failed','metadataReview','processedChunks',
    'apiRequests','apiRequestAttempts','apiFailedAttempts','inputTokens','outputTokens','driveQuotaUnits','driveDownloadedBytes',
    'attemptedModels','modelSwitchCount','lastModelError',
    'lastHttpStatus','retryAttempt','retryMaxAttempts','retryDelaySeconds','previousModel',
    'startedAt','updatedAt','finishedAt','allTargetsComplete'];
  var clean = {};
  names.forEach(function(name) {
    if (source[name] !== undefined && source[name] !== null) clean[name] = source[name];
  });
  clean.currentFileName = String(clean.currentFileName || '').slice(0, 300);
  clean.message = String(clean.message || '').slice(0, 500);
  clean.updatedAt = new Date().toISOString();
  PropertiesService.getScriptProperties().setProperty('LIVE_STATUS_JSON', JSON.stringify(clean));
  return json_({ok: true});
}

function liveStatus_() {
  var raw = PropertiesService.getScriptProperties().getProperty('LIVE_STATUS_JSON');
  if (!raw) return {status: 'NOT_INDEXED', phase: 'WAITING', message: '아직 실시간 작업 정보가 없습니다.'};
  try { return JSON.parse(raw); } catch (error) { return {status: 'UNKNOWN', message: '저장된 상태를 읽지 못했습니다.'}; }
}

function assertFolderWithinRoot_(folderId) {
  if (!folderId) throw new Error('folderId가 없습니다.');
  var rootId = requiredProperty_('DRIVE_ROOT_FOLDER_ID');
  var current = DriveApp.getFolderById(folderId);
  var visited = {};
  while (current && !visited[current.getId()]) {
    if (current.getId() === rootId) return;
    visited[current.getId()] = true;
    var parents = current.getParents();
    current = parents.hasNext() ? parents.next() : null;
  }
  throw new Error('선택한 폴더가 설정된 [book] 루트 아래에 없습니다.');
}

function previewFolder_(folderId, recursive) {
  var counts = {ok: true, targetFiles: 0, folders: 0};
  countFolder_(DriveApp.getFolderById(folderId), recursive, counts);
  return counts;
}

function countFolder_(folder, recursive, counts) {
  var files = folder.getFiles();
  while (files.hasNext()) {
    var name = files.next().getName().toLowerCase();
    if (name.endsWith('.txt') || name.endsWith('.epub')) counts.targetFiles++;
  }
  if (!recursive) return;
  var folders = folder.getFolders();
  while (folders.hasNext()) {
    counts.folders++;
    countFolder_(folders.next(), true, counts);
  }
}

function dispatchWorkflow_(body) {
  var owner = requiredProperty_('GITHUB_OWNER');
  var repo = requiredProperty_('GITHUB_REPO');
  var token = requiredProperty_('GITHUB_TOKEN');
  var url = 'https://api.github.com/repos/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo) + '/actions/workflows/index-books.yml/dispatches';
  var payload = {
    ref: 'main',
    inputs: {
      folder_id: String(body.folderId),
      recursive: String(body.recursive !== false),
      force_reindex: String(body.force === true),
      analysis_profile: String(body.analysisProfile || '')
    }
  };
  var response = UrlFetchApp.fetch(url, {
    method: 'post', contentType: 'application/json', payload: JSON.stringify(payload),
    headers: {Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'},
    muteHttpExceptions: true
  });
  var code = response.getResponseCode();
  if (code !== 200 && code !== 204) throw new Error('GitHub workflow 요청 실패: HTTP ' + code);
  PropertiesService.getScriptProperties().setProperty('LIVE_STATUS_JSON', JSON.stringify({
    status: 'QUEUED', phase: 'QUEUED', message: 'GitHub Actions 실행을 요청했습니다.',
    currentFileName: '', currentFileIndex: 0, totalFiles: 0, updatedAt: new Date().toISOString()
  }));
  var result = code === 200 ? JSON.parse(response.getContentText() || '{}') : {};
  return {ok: true, runId: result.workflow_run_id || null, runUrl: result.html_url || null};
}

function handleCallback_(body) {
  assertCallbackSecret_(body);
  handleProgress_({callbackSecret: body.callbackSecret, progress: body});
  if (body.status !== 'COMPLETE' || body.allTargetsComplete !== true) return json_({ok: true, emailSent: false});
  var recipient = PropertiesService.getScriptProperties().getProperty('COMPLETION_EMAIL') || 'narepheus@gmail.com';
  var subject = '[Book Indexer] 인덱싱 완료 - ' + (body.folderName || body.folderId || '선택 폴더');
  var message = [
    '대상 폴더: ' + (body.folderName || '') + ' (' + (body.folderId || '') + ')',
    '전체 파일: ' + (body.totalFiles || 0), '완료: ' + (body.complete || 0),
    '건너뜀: ' + (body.skipped || 0), '실패: ' + (body.failed || 0),
    '처리 chunk: ' + (body.processedChunks || 0), '사용 모델: ' + (body.model || ''),
    '시작: ' + (body.startedAt || ''), '완료: ' + (body.finishedAt || ''),
    'GitHub Pages: ' + (body.pagesUrl || '')
  ].join('\n');
  // Use the narrow Apps Script mail-sending service; no mailbox read/write API.
  MailApp.sendEmail(recipient, subject, message);
  return json_({ok: true, emailSent: true});
}

function requiredProperty_(name) {
  var value = PropertiesService.getScriptProperties().getProperty(name);
  if (!value) throw new Error('Script Property가 없습니다: ' + name);
  return value;
}

function json_(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}
