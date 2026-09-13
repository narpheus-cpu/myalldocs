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
    if (body.route === 'update-metadata') return json_(updateMetadata_(body));
    assertFolderWithinRoot_(body.folderId);
    if (body.route === 'preview') return json_(previewFolder_(body.folderId, body.recursive !== false));
    if (body.route === 'dispatch') return json_(dispatchWorkflow_(body));
    if (body.route === 'dispatch-selected') return json_(dispatchWorkflow_(body));
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
  if (!/^[A-Za-z0-9._-]{20,300}$/.test(key)) throw new Error('Gemini API Key 형식이 올바르지 않습니다.');
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

function updateMetadata_(body) {
  var driveFileId = String(body.driveFileId || '').trim();
  if (!/^[A-Za-z0-9_-]{10,200}$/.test(driveFileId)) throw new Error('원본 파일 ID가 올바르지 않습니다.');
  assertFileWithinRoot_(driveFileId);
  var raw = body.override || {};
  var allowedProfiles = ['fiction','drama','poetry','academic','philosophy','history_biography','science_technical','essay_general_nonfiction','practical_manual','mixed_anthology','unknown'];
  var override = {
    title: String(raw.title || '').trim().slice(0, 300),
    author: String(raw.author || '').trim().slice(0, 300),
    genre: String(raw.genre || '').trim().slice(0, 100),
    documentType: String(raw.documentType || 'unknown').trim(),
    tags: normalizeTags_(raw.tags)
  };
  if (!override.title) throw new Error('작품명을 입력하세요.');
  if (allowedProfiles.indexOf(override.documentType) < 0) throw new Error('지원하지 않는 문서 유형입니다.');

  var owner = requiredProperty_('GITHUB_OWNER');
  var repo = requiredProperty_('GITHUB_REPO');
  var token = requiredProperty_('GITHUB_TOKEN');
  var path = 'data/metadata-overrides.json';
  var url = 'https://api.github.com/repos/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo) + '/contents/' + path;
  var headers = {Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'};
  var currentResponse = UrlFetchApp.fetch(url + '?ref=main', {headers: headers, muteHttpExceptions: true});
  if (currentResponse.getResponseCode() !== 200) throw new Error('기존 수동 수정값을 읽지 못했습니다: HTTP ' + currentResponse.getResponseCode());
  var currentFile = JSON.parse(currentResponse.getContentText() || '{}');
  var decoded = Utilities.newBlob(Utilities.base64Decode(String(currentFile.content || '').replace(/\s/g, ''))).getDataAsString('UTF-8');
  var document = JSON.parse(decoded || '{}');
  document.description = document.description || 'driveFileId별 수동 수정값. 이 파일은 자동 인덱싱이 덮어쓰지 않습니다.';
  document.byDriveFileId = document.byDriveFileId || {};
  document.byDriveFileId[driveFileId] = override;
  var encoded = Utilities.base64Encode(Utilities.newBlob(JSON.stringify(document, null, 2) + '\n', 'application/json', 'metadata-overrides.json').getBytes());
  var updateResponse = UrlFetchApp.fetch(url, {
    method: 'put', contentType: 'application/json', headers: headers, muteHttpExceptions: true,
    payload: JSON.stringify({message: 'Update manual book metadata', content: encoded, sha: currentFile.sha, branch: 'main'})
  });
  var code = updateResponse.getResponseCode();
  if (code !== 200 && code !== 201) throw new Error('수동 수정값 저장에 실패했습니다: HTTP ' + code);
  var result = JSON.parse(updateResponse.getContentText() || '{}');
  return {ok: true, override: override, commitUrl: result.commit && result.commit.html_url || ''};
}

function handleRuntimeKey_(body) {
  assertCallbackSecret_(body);
  var key = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY') || '';
  var selected = [];
  var selectionId = String(body.selectionId || '');
  if (selectionId) {
    var raw = PropertiesService.getScriptProperties().getProperty('INDEX_SELECTION_' + selectionId);
    if (raw) {
      try {
        var record = JSON.parse(raw);
        if (record.fileIds) selected = record.fileIds;
        else for (var index = 0; index < Number(record.chunks || 0); index++) {
          var chunk = PropertiesService.getScriptProperties().getProperty('INDEX_SELECTION_' + selectionId + '_' + index);
          selected = selected.concat(JSON.parse(chunk || '[]'));
        }
      } catch (error) { selected = []; }
    }
  }
  return json_({ok: true, geminiApiKey: key, selectedFileIds: selected});
}

function handleProgress_(body) {
  assertCallbackSecret_(body);
  var source = body.progress || {};
  var names = ['status','phase','message','model','folderId','folderName','runId','runUrl','currentFileName','currentFileIndex','totalFiles',
    'currentChunk','totalChunks','complete','skipped','failed','metadataReview','processedChunks',
    'apiRequests','apiSuccessfulRequests','apiRequestAttempts','apiFailedAttempts','inputTokens','outputTokens','driveQuotaUnits','driveDownloadedBytes',
    'attemptedModels','modelSwitchCount','lastModelError',
    'modelCycle','maxModelCycles','modelCycleRestarts',
    'invalidJsonResponses','lastError',
    'lastHttpStatus','retryAttempt','retryMaxAttempts','retryDelaySeconds','previousModel',
    'startedAt','updatedAt','finishedAt','allTargetsComplete'];
  var clean = {};
  names.forEach(function(name) {
    if (source[name] !== undefined && source[name] !== null) clean[name] = source[name];
  });
  clean.currentFileName = String(clean.currentFileName || '').slice(0, 300);
  clean.message = String(clean.message || '').slice(0, 500);
  var previous = liveStatus_();
  ['folderId','folderName','runId','runUrl','startedAt'].forEach(function(name) {
    if (!clean[name] && previous[name]) clean[name] = previous[name];
  });
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

function assertFileWithinRoot_(fileId) {
  var file = DriveApp.getFileById(fileId);
  var parents = file.getParents();
  while (parents.hasNext()) {
    try {
      assertFolderWithinRoot_(parents.next().getId());
      return;
    } catch (error) {
      // A Drive file can have more than one parent; inspect every parent before rejecting it.
    }
  }
  throw new Error('선택한 파일이 설정된 [book] 루트 아래에 없습니다.');
}

function previewFolder_(folderId, recursive) {
  var counts = {ok: true, targetFiles: 0, folders: 0};
  countFolder_(DriveApp.getFolderById(folderId), recursive, counts);
  return counts;
}

function countFolder_(folder, recursive, counts) {
  var files = folder.getFiles();
  while (files.hasNext()) {
    var file = files.next();
    var name = file.getName().toLowerCase();
    var mimeType = file.getMimeType();
    if (mimeType === 'text/plain' || mimeType === 'application/epub+zip' || name.endsWith('.txt') || name.endsWith('.epub')) counts.targetFiles++;
  }
  if (!recursive) return;
  var folders = folder.getFolders();
  while (folders.hasNext()) {
    counts.folders++;
    countFolder_(folders.next(), true, counts);
  }
}

function dispatchWorkflow_(body) {
  var lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    var owner = requiredProperty_('GITHUB_OWNER');
    var repo = requiredProperty_('GITHUB_REPO');
    var token = requiredProperty_('GITHUB_TOKEN');
    var current = liveStatus_();
    var active = current.status === 'QUEUED' || current.status === 'RUNNING';
    var updated = Date.parse(current.updatedAt || '');
    var fresh = !isNaN(updated) && (Date.now() - updated) < 22500000; // workflow timeout + margin
    if (active && fresh) {
      if (Date.now() - updated < 120000) {
        return {ok: true, alreadyRunning: true, message: '방금 요청한 인덱싱 작업이 시작되기를 기다리고 있습니다.'};
      }
      var latest = latestIndexRun_(owner, repo, token);
      if (!latest || latest.status !== 'completed') {
        return {ok: true, alreadyRunning: true, message: '이미 인덱싱 작업이 실행 또는 대기 중입니다.', runId: latest && latest.id || '', runUrl: latest && latest.html_url || ''};
      }
    }

    var url = 'https://api.github.com/repos/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo) + '/actions/workflows/index-books.yml/dispatches';
    var selectionId = '';
    if (body.route === 'dispatch-selected') {
      var fileIds = normalizeFileIds_(body.fileIds);
      if (!fileIds.length) throw new Error('인덱싱할 파일을 선택하세요.');
      if (fileIds.length > 1000) throw new Error('한 번에 선택할 수 있는 파일은 최대 1,000개입니다.');
      fileIds.forEach(assertFileWithinRoot_);
      selectionId = Utilities.getUuid();
      saveSelection_(selectionId, fileIds, String(body.folderId));
    }
    var payload = {
      ref: 'main',
      inputs: {
        folder_id: String(body.folderId),
        recursive: String(body.recursive !== false),
        force_reindex: String(body.force === true),
        analysis_profile: String(body.analysisProfile || ''),
        selection_id: selectionId
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
      folderId: String(body.folderId), folderName: String(body.folderName || ''), currentFileName: '', currentFileIndex: 0,
      totalFiles: 0, updatedAt: new Date().toISOString()
    }));
    Utilities.sleep(1200);
    var result = code === 200 ? JSON.parse(response.getContentText() || '{}') : (latestIndexRun_(owner, repo, token) || {});
    return {ok: true, alreadyRunning: false, runId: result.workflow_run_id || result.id || null, runUrl: result.html_url || null};
  } finally {
    lock.releaseLock();
  }
}

function latestIndexRun_(owner, repo, token) {
  var url = 'https://api.github.com/repos/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo) + '/actions/workflows/index-books.yml/runs?event=workflow_dispatch&per_page=1';
  var response = UrlFetchApp.fetch(url, {
    headers: {Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'},
    muteHttpExceptions: true
  });
  if (response.getResponseCode() !== 200) return null;
  var runs = JSON.parse(response.getContentText() || '{}').workflow_runs || [];
  return runs.length ? runs[0] : null;
}

function normalizeTags_(value) {
  var raw = Array.isArray(value) ? value : String(value || '').split(/[,\n]/);
  var seen = {};
  var tags = [];
  raw.forEach(function(item) {
    var tag = String(item || '').replace(/^#+/, '').replace(/\s+/g, ' ').trim().slice(0, 60);
    var key = tag.toLowerCase();
    if (!tag || seen[key]) return;
    seen[key] = true;
    tags.push(tag);
  });
  return tags.slice(0, 20);
}

function normalizeFileIds_(value) {
  if (!Array.isArray(value)) return [];
  var seen = {};
  return value.map(function(item) { return String(item || '').trim(); }).filter(function(item) {
    if (!/^[A-Za-z0-9_-]{10,200}$/.test(item) || seen[item]) return false;
    seen[item] = true;
    return true;
  });
}

function saveSelection_(selectionId, fileIds, folderId) {
  var properties = PropertiesService.getScriptProperties();
  cleanupSelections_(properties);
  var chunks = [];
  for (var offset = 0; offset < fileIds.length; offset += 150) chunks.push(fileIds.slice(offset, offset + 150));
  var values = {};
  values['INDEX_SELECTION_' + selectionId] = JSON.stringify({chunks: chunks.length, folderId: folderId, createdAt: new Date().toISOString()});
  chunks.forEach(function(chunk, index) {
    values['INDEX_SELECTION_' + selectionId + '_' + index] = JSON.stringify(chunk);
  });
  properties.setProperties(values);
}

function cleanupSelections_(properties) {
  var values = properties.getProperties();
  var cutoff = Date.now() - (7 * 24 * 60 * 60 * 1000);
  Object.keys(values).forEach(function(key) {
    if (!/^INDEX_SELECTION_[A-Za-z0-9-]+$/.test(key)) return;
    try {
      var metadata = JSON.parse(values[key] || '{}');
      if (!metadata.createdAt || new Date(metadata.createdAt).getTime() >= cutoff) return;
      var chunks = Math.max(0, Number(metadata.chunks) || 0);
      properties.deleteProperty(key);
      for (var index = 0; index < chunks; index += 1) properties.deleteProperty(key + '_' + index);
    } catch (error) {
      properties.deleteProperty(key);
    }
  });
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
