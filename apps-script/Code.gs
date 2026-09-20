/**
 * Personal Book Knowledge Base relay.
 * Script Properties required: GITHUB_OWNER, GITHUB_REPO,
 * CALLBACK_SECRET, COMPLETION_EMAIL, DRIVE_ROOT_FOLDER_ID, SERVICE_ACCOUNT_EMAIL.
 * GITHUB_TOKEN is optional. Without a valid token, the scheduled queue worker
 * picks up persisted uploads instead of failing the upload.
 */
function doGet() {
  return json_({ok: true, service: 'book-indexer-relay'});
}

/**
 * Run once from the Apps Script editor after installation or a scope change.
 * It requests the Drive write grant and verifies the private management folder.
 */
function setupPrivateStorage() {
  return {ok: true, folderId: ensureManagementFolderId_()};
}

/**
 * Run once from the Apps Script editor after deployment. This installs the
 * free 15-minute clock trigger, finds the single books-json folder, and starts
 * the first background scan immediately. GitHub's schedule remains a backup.
 */
function installCanonicalImportAutomation() {
  var properties = PropertiesService.getScriptProperties();
  var folderId = String(properties.getProperty('CANONICAL_IMPORT_FOLDER_ID') || '') || discoverCanonicalImportFolder_();
  if (!folderId) throw new Error('이름이 books-json인 폴더가 하나만 있어야 합니다. 인덱싱 화면에서 폴더를 직접 선택할 수도 있습니다.');
  var trigger = ensureCanonicalImportTrigger_();
  var run = scheduledCanonicalImportTick_();
  return {ok: true, trigger: trigger, run: run, canonicalImport: canonicalImportStatus_()};
}

function doPost(e) {
  try {
    var body = JSON.parse((e.postData && e.postData.contents) || '{}');
    if (body.route === 'callback' || body.event === 'indexing-complete') return handleCallback_(body);
    if (body.route === 'progress') return handleProgress_(body);
    if (body.route === 'runtime-key') return handleRuntimeKey_(body);
    if (body.route === 'private-queue') return handlePrivateQueue_(body);
    if (body.route === 'queue-state') return handleQueueState_(body);
    if (body.route === 'queue-result') return handleQueueResult_(body);
    // A private upload is authorized once at upload-start.  Requiring a
    // userinfo lookup for every binary part made larger uploads depend on
    // dozens of back-to-back Apps Script executions.  Protocol v2 uses the
    // unguessable per-session token for subsequent parts and finish calls.
    if (body.route === 'upload-part' && body.uploadToken) return json_(savePrivateUploadPart_(body));
    if (body.route === 'upload-finish' && body.uploadToken) return json_(finishPrivateUpload_(body));
    assertAuthorizedUser_(body);
    if (body.route === 'status') return json_({ok: true, progress: liveStatus_(), geminiKey: geminiKeyStatus_(), githubDispatch: githubDispatchStatus_(), contentSave: contentSaveStatus_(), canonicalImport: canonicalImportStatus_()});
    if (body.route === 'upload-capabilities') return json_({ok: true, idempotentUploads: true, uploadProtocolVersion: 2, partBytes: 2097152, partDelayMs: 400});
    if (body.route === 'update-api-key') return json_(updateApiKey_(body));
    if (body.route === 'update-github-token') return json_(updateGitHubToken_(body));
    if (body.route === 'configure-canonical-import') return json_(configureCanonicalImport_(body));
    if (body.route === 'run-canonical-import') return json_(runCanonicalImportNow_());
    if (body.route === 'disable-canonical-import') return json_(disableCanonicalImport_());
    if (body.route === 'retry-queue-dispatch') return json_({ok: true, dispatch: dispatchQueueWorkflow_()});
    if (body.route === 'update-metadata') return json_(updateMetadata_(body));
    if (body.route === 'update-book-content') return json_(handleBookContentUpdate_(body));
    if (body.route === 'update-source-link') return json_(handleSourceLinkUpdate_(body));
    if (body.route === 'delete-books') return json_(deleteBooks_(body));
    if (body.route === 'upload-start') return json_(startPrivateUpload_(body));
    if (body.route === 'upload-part') return json_(savePrivateUploadPart_(body));
    if (body.route === 'upload-finish') return json_(finishPrivateUpload_(body));
    if (body.route === 'queue-admin') {
      var queue = queueAdmin_(body);
      return json_({ok: true, queue: queue, currentManifestId: queueViewManifestId_()});
    }
    if (body.route === 'queue-retry') return json_(retryQueue_(body));
    assertFolderWithinRoot_(body.folderId);
    if (body.route === 'preview') return json_(previewFolder_(body.folderId, body.recursive !== false));
    if (body.route === 'dispatch') return json_(dispatchWorkflow_(body));
    if (body.route === 'dispatch-selected') return json_(dispatchWorkflow_(body));
    throw new Error('Unknown route');
  } catch (error) {
    console.error(JSON.stringify({route: body && body.route || '', error: String(error && error.stack || error)}));
    return json_({ok: false, error: String(error && error.message || error)});
  }
}

function startPrivateUpload_(body) {
  var requestId = String(body.requestId || '');
  if (!/^[A-Za-z0-9-]{12,100}$/.test(requestId)) throw new Error('업로드 요청 번호가 올바르지 않습니다.');
  var lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    var properties = PropertiesService.getScriptProperties();
    var previous = properties.getProperty('UPLOAD_REQUEST_' + requestId);
    if (previous) return JSON.parse(previous);
    var kind = String(body.kind || '');
    if (['catalog-jsonl', 'canonical-json', 'content-edit', 'source-link'].indexOf(kind) < 0) throw new Error('지원하지 않는 업로드 종류입니다.');
    var filename = String(body.filename || '').trim().slice(0, 200);
    var size = Math.max(0, Number(body.size) || 0);
    var partCount = Math.max(1, Number(body.partCount) || 0);
    if (!filename) throw new Error('파일명이 없습니다.');
    if (size < 1 || size > 104857600) throw new Error('업로드 파일은 100MB 이하여야 합니다.');
    if (partCount > 1000) throw new Error('업로드 조각이 너무 많습니다.');
    if (kind === 'catalog-jsonl' && !/\.jsonl$/i.test(filename)) throw new Error('JSONL 파일을 선택하세요.');
    if ((kind === 'canonical-json' || kind === 'content-edit' || kind === 'source-link') && !/\.json$/i.test(filename)) throw new Error('JSON 파일을 선택하세요.');
    var id = Utilities.getUuid();
    var parentId = ensureManagementFolderId_();
    var folder = driveCreateMetadata_({name: 'upload-' + id, mimeType: 'application/vnd.google-apps.folder', parents: [parentId]});
    driveEnsureEditor_(folder.id, requiredProperty_('SERVICE_ACCOUNT_EMAIL').trim());
    var uploadToken = Utilities.getUuid() + Utilities.getUuid();
    var record = {id: id, uploadToken: uploadToken, kind: kind, filename: filename, size: size, partCount: partCount, folderId: folder.id, createdAt: new Date().toISOString()};
    properties.setProperty('UPLOAD_SESSION_' + id, JSON.stringify(record));
    var response = {ok: true, uploadId: id, uploadToken: uploadToken, partBytes: 2097152};
    properties.setProperty('UPLOAD_REQUEST_' + requestId, JSON.stringify(response));
    return response;
  } finally {
    lock.releaseLock();
  }
}

function savePrivateUploadPart_(body) {
  var record = privateUploadRecord_(body.uploadId, body.uploadToken);
  var index = Number(body.index);
  if (!Number.isInteger(index) || index < 0 || index >= record.partCount) throw new Error('업로드 조각 번호가 올바르지 않습니다.');
  var encoded = String(body.base64 || '');
  if (!encoded || encoded.length > 3000000) throw new Error('업로드 조각 크기가 올바르지 않습니다.');
  var bytes = Utilities.base64Decode(encoded);
  if (bytes.length > 2097152) throw new Error('업로드 조각은 2MB 이하여야 합니다.');
  var name = 'part-' + String(index).padStart(6, '0') + '.bin';
  var existing = driveListChildren_(record.folderId).filter(function(item) { return item.name === name; });
  if (existing.length) return {ok: true, index: index, duplicate: true};
  driveCreateFile_({name: name, parents: [record.folderId]}, bytes, 'application/octet-stream');
  return {ok: true, index: index};
}

function finishPrivateUpload_(body) {
  var lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
    var completedKey = 'UPLOAD_FINISHED_' + String(body.uploadId || '');
    var completed = PropertiesService.getScriptProperties().getProperty(completedKey);
    if (completed) return JSON.parse(completed);
    var record = privateUploadRecord_(body.uploadId, body.uploadToken);
    var children = driveListChildren_(record.folderId);
    var parts = children.map(function(file) {
      var match = /^part-(\d{6})\.bin$/.exec(file.name || '');
      return match ? {index: Number(match[1]), fileId: file.id, size: Number(file.size || 0)} : null;
    }).filter(Boolean).sort(function(a, b) { return a.index - b.index; });
    if (parts.length !== record.partCount) throw new Error('업로드 조각이 모두 도착하지 않았습니다.');
    for (var index = 0; index < parts.length; index++) if (parts[index].index !== index) throw new Error('업로드 조각 순서가 불완전합니다.');
    var total = parts.reduce(function(sum, part) { return sum + part.size; }, 0);
    if (total !== record.size) throw new Error('업로드 크기가 원본과 일치하지 않습니다.');
    var existingManifest = children.filter(function(file) { return file.name === 'queue-manifest.json'; })[0];
    var manifestFile = existingManifest;
    if (!manifestFile) {
      var initialState = children.filter(function(file) { return file.name === 'queue-state.json'; })[0] ||
        driveCreateFile_({name: 'queue-state.json', parents: [record.folderId]}, Utilities.newBlob('{}').getBytes(), 'application/json');
      var manifest = {
        schemaVersion: 1, kind: record.kind, originalFilename: record.filename,
        sourceRootFolderId: requiredProperty_('DRIVE_ROOT_FOLDER_ID'), sessionFolderId: record.folderId,
        parts: parts, stateFileId: initialState.id, createdAt: record.createdAt
      };
      manifestFile = driveCreateFile_({name: 'queue-manifest.json', parents: [record.folderId]}, Utilities.newBlob(JSON.stringify(manifest, null, 2)).getBytes(), 'application/json');
    }
    appendQueueManifest_(manifestFile.id);
    var response = {ok: true, queued: true, manifestId: manifestFile.id, dispatch: body.skipDispatch === true ? {requested: false, deferred: true} : dispatchQueueWorkflow_(manifestFile.id)};
    PropertiesService.getScriptProperties().setProperty(completedKey, JSON.stringify(response));
    PropertiesService.getScriptProperties().deleteProperty('UPLOAD_SESSION_' + record.id);
    return response;
  } finally {
    lock.releaseLock();
  }
}

/**
 * Connect one Drive folder as the inbox for completed canonical JSON files.
 * The browser only sends the selected folder id. Files stay in Drive and are
 * copied server-side into the existing private queue, so page refreshes and
 * browser upload timeouts cannot interrupt the import.
 */
function configureCanonicalImport_(body) {
  var folderId = String(body.folderId || '').trim();
  if (!/^[A-Za-z0-9_-]{10,200}$/.test(folderId)) throw new Error('자동 가져오기 폴더가 올바르지 않습니다.');
  var metadata = Drive.Files.get(folderId, {fields: 'id,name,mimeType', supportsAllDrives: true});
  if (String(metadata.mimeType || '') !== 'application/vnd.google-apps.folder') throw new Error('Google Drive 폴더를 선택하세요.');
  var properties = PropertiesService.getScriptProperties();
  properties.setProperties({
    CANONICAL_IMPORT_FOLDER_ID: folderId,
    CANONICAL_IMPORT_FOLDER_NAME: String(metadata.name || body.folderName || 'books-json').slice(0, 300),
    CANONICAL_IMPORT_ENABLED_AT: new Date().toISOString()
  });
  ensureCanonicalImportTrigger_();
  var dispatch = requestCanonicalImportScan_();
  return {
    ok: true, canonicalImport: canonicalImportStatus_(),
    scan: {queued: 0, message: '폴더를 연결했습니다. 백엔드에서 새 JSON 확인을 요청했습니다.'},
    dispatch: dispatch
  };
}

function runCanonicalImportNow_() {
  if (!canonicalImportStatus_().enabled) throw new Error('먼저 books-json 폴더를 연결하세요.');
  return {
    ok: true, canonicalImport: canonicalImportStatus_(),
    scan: {queued: 0, message: '백엔드에 새 JSON 확인을 요청했습니다.'},
    dispatch: requestCanonicalImportScan_()
  };
}

function requestCanonicalImportScan_() {
  var owner = requiredProperty_('GITHUB_OWNER');
  var repo = requiredProperty_('GITHUB_REPO');
  var token = String(PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN') || '').trim();
  if (!githubTokenLooksValid_(token)) return {requested: false, scheduledFallback: true, reason: 'missing_or_invalid_token', code: 0, message: githubDispatchFailureMessage_(0, 'missing_or_invalid_token')};
  // An empty manifest id deliberately makes queue_context call private-queue,
  // where the Drive folder scan runs outside the browser request.
  var result = requestQueueWorkflow_(owner, repo, token, '');
  if (!result.ok) return {requested: false, scheduledFallback: true, reason: result.reason, code: result.code, message: githubDispatchFailureMessage_(result.code, result.reason)};
  return {requested: true, scheduledFallback: false};
}

function disableCanonicalImport_() {
  var properties = PropertiesService.getScriptProperties();
  removeCanonicalImportTriggers_();
  properties.deleteProperty('CANONICAL_IMPORT_FOLDER_ID');
  properties.deleteProperty('CANONICAL_IMPORT_FOLDER_NAME');
  properties.deleteProperty('CANONICAL_IMPORT_ENABLED_AT');
  return {ok: true, canonicalImport: canonicalImportStatus_()};
}

function canonicalImportStatus_() {
  var properties = PropertiesService.getScriptProperties();
  var folderId = String(properties.getProperty('CANONICAL_IMPORT_FOLDER_ID') || '');
  var last = {};
  try { last = JSON.parse(properties.getProperty('CANONICAL_IMPORT_LAST_RESULT') || '{}'); }
  catch (ignored) { last = {}; }
  return {
    enabled: Boolean(folderId),
    folderId: folderId,
    folderName: String(properties.getProperty('CANONICAL_IMPORT_FOLDER_NAME') || ''),
    enabledAt: String(properties.getProperty('CANONICAL_IMPORT_ENABLED_AT') || ''),
    intervalMinutes: 15,
    triggerInstalled: properties.getProperty('CANONICAL_IMPORT_TRIGGER_INSTALLED') === 'true',
    triggerInstalledAt: String(properties.getProperty('CANONICAL_IMPORT_TRIGGER_INSTALLED_AT') || ''),
    lastTriggerAt: String(properties.getProperty('CANONICAL_IMPORT_TRIGGER_LAST_AT') || ''),
    lastTriggerStatus: String(properties.getProperty('CANONICAL_IMPORT_TRIGGER_LAST_STATUS') || ''),
    lastTriggerMessage: String(properties.getProperty('CANONICAL_IMPORT_TRIGGER_LAST_MESSAGE') || ''),
    lastScanAt: String(last.scannedAt || ''),
    lastScanned: Number(last.scanned || 0),
    lastQueued: Number(last.queued || 0),
    lastSkipped: Number(last.skipped || 0),
    lastErrors: Number(last.errors || 0),
    lastMessage: String(last.message || '')
  };
}

function ensureCanonicalImportTrigger_() {
  var handler = 'scheduledCanonicalImportTick_';
  var matching = ScriptApp.getProjectTriggers().filter(function(trigger) {
    return trigger.getHandlerFunction() === handler;
  });
  var kept = matching.shift();
  matching.forEach(function(trigger) { ScriptApp.deleteTrigger(trigger); });
  if (!kept) kept = ScriptApp.newTrigger(handler).timeBased().everyMinutes(15).create();
  var installedAt = new Date().toISOString();
  PropertiesService.getScriptProperties().setProperties({
    CANONICAL_IMPORT_TRIGGER_INSTALLED: 'true',
    CANONICAL_IMPORT_TRIGGER_INSTALLED_AT: installedAt,
    CANONICAL_IMPORT_TRIGGER_UID: String(kept.getUniqueId() || '')
  });
  return {installed: true, intervalMinutes: 15, installedAt: installedAt};
}

function removeCanonicalImportTriggers_() {
  ScriptApp.getProjectTriggers().forEach(function(trigger) {
    if (trigger.getHandlerFunction() === 'scheduledCanonicalImportTick_') ScriptApp.deleteTrigger(trigger);
  });
  var properties = PropertiesService.getScriptProperties();
  properties.deleteProperty('CANONICAL_IMPORT_TRIGGER_INSTALLED');
  properties.deleteProperty('CANONICAL_IMPORT_TRIGGER_INSTALLED_AT');
  properties.deleteProperty('CANONICAL_IMPORT_TRIGGER_UID');
}

/**
 * Primary, no-billing scheduler. It scans Drive itself instead of waiting for
 * GitHub's best-effort cron, then asks the existing free standard runner to
 * process the first queued item. The importer lock prevents overlapping scans.
 */
function scheduledCanonicalImportTick_() {
  var properties = PropertiesService.getScriptProperties();
  var startedAt = new Date().toISOString();
  properties.setProperties({
    CANONICAL_IMPORT_TRIGGER_LAST_AT: startedAt,
    CANONICAL_IMPORT_TRIGGER_LAST_STATUS: 'RUNNING',
    CANONICAL_IMPORT_TRIGGER_LAST_MESSAGE: 'books-json 폴더를 확인하는 중입니다.'
  });
  try {
    var scan = importCanonicalFolder_({limit: 10});
    var active = queueIds_('PRIVATE_QUEUE_MANIFEST_IDS');
    var dispatch = {requested: false, scheduledFallback: false, reason: active.length ? 'already_running_or_queued' : 'empty_queue'};
    var live = liveStatus_();
    var updated = Date.parse(live.updatedAt || '');
    var busy = ['QUEUED', 'RUNNING'].indexOf(String(live.status || '')) >= 0 &&
      !isNaN(updated) && (Date.now() - updated) < 22500000;
    if (active.length && !busy) {
      var preferred = scan.queuedManifestIds && scan.queuedManifestIds.length ? scan.queuedManifestIds[0] : active[0];
      dispatch = dispatchQueueWorkflow_(preferred);
    }
    var status = scan.errors ? 'WARNING' : 'SUCCESS';
    var message = scan.message || (active.length ? '등록된 대기열을 확인했습니다.' : '새 JSON이 없습니다.');
    if (busy && active.length) message += ' 기존 대기열이 실행 중이므로 완료 후 차례로 이어집니다.';
    properties.setProperties({
      CANONICAL_IMPORT_TRIGGER_LAST_STATUS: status,
      CANONICAL_IMPORT_TRIGGER_LAST_MESSAGE: message
    });
    return {ok: !scan.errors, scan: publicCanonicalImportResult_(scan), dispatch: dispatch, busy: busy};
  } catch (error) {
    var detail = String(error && error.message || error).slice(0, 500);
    properties.setProperties({
      CANONICAL_IMPORT_TRIGGER_LAST_STATUS: 'ERROR',
      CANONICAL_IMPORT_TRIGGER_LAST_MESSAGE: detail
    });
    throw error;
  }
}

function publicCanonicalImportResult_(result) {
  return {
    scannedAt: result.scannedAt, scanned: result.scanned, queued: result.queued,
    skipped: result.skipped, errors: result.errors, message: result.message
  };
}

function discoverCanonicalImportFolder_() {
  var response = Drive.Files.list({
    q: "name = 'books-json' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
    fields: 'files(id,name)', pageSize: 3, orderBy: 'createdTime asc', spaces: 'drive'
  });
  var folders = response.files || [];
  if (folders.length !== 1) return '';
  var properties = PropertiesService.getScriptProperties();
  properties.setProperties({
    CANONICAL_IMPORT_FOLDER_ID: String(folders[0].id),
    CANONICAL_IMPORT_FOLDER_NAME: String(folders[0].name || 'books-json'),
    CANONICAL_IMPORT_ENABLED_AT: new Date().toISOString()
  });
  return String(folders[0].id);
}

function importCanonicalFolder_(options) {
  options = options || {};
  var limit = Math.min(20, Math.max(1, Number(options.limit) || 20));
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var properties = PropertiesService.getScriptProperties();
    var folderId = String(properties.getProperty('CANONICAL_IMPORT_FOLDER_ID') || '') || discoverCanonicalImportFolder_();
    if (!folderId) {
      var missing = {
        scannedAt: new Date().toISOString(), scanned: 0, queued: 0, skipped: 0, errors: 0,
        queuedManifestIds: [], message: '이름이 books-json인 폴더가 하나만 있어야 자동으로 연결됩니다. 인덱싱 화면에서 폴더를 직접 선택할 수도 있습니다.'
      };
      properties.setProperty('CANONICAL_IMPORT_LAST_RESULT', JSON.stringify(publicCanonicalImportResult_(missing)));
      return missing;
    }
    var folder = Drive.Files.get(folderId, {fields: 'id,name,mimeType', supportsAllDrives: true});
    if (String(folder.mimeType || '') !== 'application/vnd.google-apps.folder') throw new Error('저장된 books-json 폴더를 찾을 수 없습니다.');
    properties.setProperty('CANONICAL_IMPORT_FOLDER_NAME', String(folder.name || 'books-json'));

    var history = loadCanonicalImportHistory_();
    var files = listCanonicalJsonFiles_(folderId);
    var result = {scannedAt: new Date().toISOString(), scanned: files.length, queued: 0, skipped: 0, errors: 0, queuedManifestIds: [], message: ''};
    for (var index = 0; index < files.length && result.queued < limit; index++) {
      var file = files[index];
      var fingerprint = canonicalImportFingerprint_(file);
      var previous = history.byFileId[String(file.id)] || null;
      var sameChecksum = file.md5Checksum && history.byChecksum[String(file.md5Checksum)] || null;
      if ((previous && previous.fingerprint === fingerprint) || sameChecksum) {
        result.skipped++;
        if (!previous) history.byFileId[String(file.id)] = {
          fingerprint: fingerprint, manifestId: sameChecksum.manifestId || '', filename: String(file.name || ''),
          importedAt: sameChecksum.importedAt || result.scannedAt, duplicateOf: sameChecksum.driveFileId || ''
        };
        continue;
      }
      if (Number(file.size || 0) > 104857600) {
        result.errors++;
        continue;
      }
      try {
        var manifestId = createCanonicalQueueFromDriveFile_(folderId, file, fingerprint);
        var record = {
          driveFileId: String(file.id), fingerprint: fingerprint, manifestId: manifestId,
          filename: String(file.name || ''), size: Number(file.size || 0), modifiedTime: String(file.modifiedTime || ''),
          importedAt: result.scannedAt
        };
        history.byFileId[String(file.id)] = record;
        if (file.md5Checksum) history.byChecksum[String(file.md5Checksum)] = record;
        saveCanonicalImportHistory_(history);
        result.queued++;
        result.queuedManifestIds.push(manifestId);
      } catch (error) {
        result.errors++;
        console.error('books-json import failed for ' + String(file.name || file.id) + ': ' + String(error && error.stack || error));
      }
    }
    if (result.skipped) saveCanonicalImportHistory_(history);
    result.message = result.queued ? result.queued + '개 JSON을 새 대기열에 등록했습니다.' :
      (result.errors ? '일부 JSON을 등록하지 못했습니다. 최근 결과를 확인하세요.' : '새 JSON이 없습니다. 이미 등록한 파일은 건너뛰었습니다.');
    properties.setProperty('CANONICAL_IMPORT_LAST_RESULT', JSON.stringify(publicCanonicalImportResult_(result)));
    return result;
  } catch (error) {
    var failure = {
      scannedAt: new Date().toISOString(), scanned: 0, queued: 0, skipped: 0, errors: 1,
      queuedManifestIds: [], message: String(error && error.message || error).slice(0, 500)
    };
    PropertiesService.getScriptProperties().setProperty('CANONICAL_IMPORT_LAST_RESULT', JSON.stringify(publicCanonicalImportResult_(failure)));
    return failure;
  } finally {
    lock.releaseLock();
  }
}

function listCanonicalJsonFiles_(folderId) {
  var files = [];
  var pageToken = '';
  do {
    var options = {
      q: "'" + String(folderId).replace(/'/g, "\\'") + "' in parents and trashed = false",
      fields: 'nextPageToken,files(id,name,size,mimeType,modifiedTime,createdTime,md5Checksum)',
      pageSize: 200, orderBy: 'createdTime asc,name', spaces: 'drive', supportsAllDrives: true, includeItemsFromAllDrives: true
    };
    if (pageToken) options.pageToken = pageToken;
    var response = Drive.Files.list(options);
    (response.files || []).forEach(function(file) {
      if (String(file.mimeType || '') === 'application/vnd.google-apps.folder') return;
      if (String(file.mimeType || '') === 'application/json' || /\.json$/i.test(String(file.name || ''))) files.push(file);
    });
    pageToken = String(response.nextPageToken || '');
  } while (pageToken && files.length < 5000);
  return files.slice(0, 5000);
}

function canonicalImportFingerprint_(file) {
  return file.md5Checksum ? 'md5:' + String(file.md5Checksum) :
    ['drive', String(file.id || ''), String(file.modifiedTime || ''), String(file.size || 0)].join(':');
}

function createCanonicalQueueFromDriveFile_(sourceFolderId, sourceFile, fingerprint) {
  var parentId = ensureManagementFolderId_();
  var session = driveCreateMetadata_({name: 'auto-json-' + Utilities.getUuid(), mimeType: 'application/vnd.google-apps.folder', parents: [parentId]});
  driveEnsureEditor_(session.id, requiredProperty_('SERVICE_ACCOUNT_EMAIL').trim());
  var part = driveCopyFile_(sourceFile.id, {name: 'part-000000.bin', parents: [session.id]});
  var initialState = driveCreateFile_({name: 'queue-state.json', parents: [session.id]}, Utilities.newBlob('{}').getBytes(), 'application/json');
  var createdAt = new Date().toISOString();
  var manifest = {
    schemaVersion: 1, kind: 'canonical-json', originalFilename: String(sourceFile.name || 'book.json'),
    sourceRootFolderId: requiredProperty_('DRIVE_ROOT_FOLDER_ID'), sessionFolderId: session.id,
    parts: [{index: 0, fileId: part.id, size: Number(part.size || sourceFile.size || 0)}],
    stateFileId: initialState.id, createdAt: createdAt,
    automaticImport: {folderId: String(sourceFolderId), driveFileId: String(sourceFile.id), fingerprint: fingerprint}
  };
  var manifestFile = driveCreateFile_({name: 'queue-manifest.json', parents: [session.id]}, Utilities.newBlob(JSON.stringify(manifest, null, 2)).getBytes(), 'application/json');
  appendQueueManifest_(manifestFile.id);
  return manifestFile.id;
}

function canonicalImportHistoryFile_() {
  var properties = PropertiesService.getScriptProperties();
  var fileId = String(properties.getProperty('CANONICAL_IMPORT_HISTORY_FILE_ID') || '');
  if (fileId) {
    try { Drive.Files.get(fileId, {fields: 'id', supportsAllDrives: true}); return fileId; }
    catch (ignored) { fileId = ''; }
  }
  var parentId = ensureManagementFolderId_();
  var existing = driveListChildren_(parentId).filter(function(file) { return file.name === 'books-json-import-history.json'; })[0];
  if (existing) fileId = String(existing.id);
  else fileId = String(driveCreateFile_({name: 'books-json-import-history.json', parents: [parentId]}, Utilities.newBlob('{"schemaVersion":1,"byFileId":{},"byChecksum":{}}').getBytes(), 'application/json').id);
  properties.setProperty('CANONICAL_IMPORT_HISTORY_FILE_ID', fileId);
  return fileId;
}

function loadCanonicalImportHistory_() {
  try {
    var value = JSON.parse(DriveApp.getFileById(canonicalImportHistoryFile_()).getBlob().getDataAsString('UTF-8') || '{}');
    value.byFileId = value.byFileId && typeof value.byFileId === 'object' ? value.byFileId : {};
    value.byChecksum = value.byChecksum && typeof value.byChecksum === 'object' ? value.byChecksum : {};
    return value;
  } catch (error) {
    throw driveAdvancedError_('books-json 중복 기록을 읽지 못했습니다', error);
  }
}

function saveCanonicalImportHistory_(history) {
  history.schemaVersion = 1;
  history.updatedAt = new Date().toISOString();
  driveUpdateFileContent_(canonicalImportHistoryFile_(), JSON.stringify(history, null, 2), 'application/json');
}

function privateUploadRecord_(uploadId, uploadToken) {
  var id = String(uploadId || '');
  if (!/^[A-Za-z0-9-]{20,80}$/.test(id)) throw new Error('업로드 세션이 올바르지 않습니다.');
  var raw = PropertiesService.getScriptProperties().getProperty('UPLOAD_SESSION_' + id);
  if (!raw) throw new Error('업로드 세션이 만료되었거나 없습니다.');
  var record = JSON.parse(raw);
  if (record.uploadToken && String(uploadToken || '') !== String(record.uploadToken)) throw new Error('업로드 세션 인증값이 올바르지 않습니다.');
  return record;
}

function ensureManagementFolderId_() {
  var properties = PropertiesService.getScriptProperties();
  var serviceAccountEmail = requiredProperty_('SERVICE_ACCOUNT_EMAIL').trim();
  if (!/^[^@\s]+@[^@\s]+\.iam\.gserviceaccount\.com$/i.test(serviceAccountEmail)) {
    throw new Error('SERVICE_ACCOUNT_EMAIL에는 books 폴더에 공유된 서비스 계정의 client_email 주소를 입력하세요. 일반 Gmail 주소는 사용할 수 없습니다.');
  }
  var id = properties.getProperty('PRIVATE_MANAGEMENT_FOLDER_ID') || '';
  if (id) {
    try { driveGetMetadata_(id); return id; } catch (error) { id = ''; }
  }
  var folder = driveCreateMetadata_({name: '서재지도_비공개_관리', mimeType: 'application/vnd.google-apps.folder'});
  driveShareEditor_(folder.id, serviceAccountEmail);
  properties.setProperty('PRIVATE_MANAGEMENT_FOLDER_ID', folder.id);
  return folder.id;
}

function queueIds_(name) {
  try { return JSON.parse(PropertiesService.getScriptProperties().getProperty(name) || '[]'); }
  catch (error) { return []; }
}

function appendQueueManifest_(id) {
  var properties = PropertiesService.getScriptProperties();
  var ids = queueIds_('PRIVATE_QUEUE_MANIFEST_IDS').filter(function(item) { return item !== id; });
  ids.push(id);
  properties.setProperty('PRIVATE_QUEUE_MANIFEST_IDS', JSON.stringify(ids.slice(-200)));
  properties.setProperty('LATEST_QUEUE_MANIFEST_ID', String(id));
}

function queueManifestSnapshot_(id) {
  var manifest = JSON.parse(DriveApp.getFileById(String(id)).getBlob().getDataAsString('UTF-8'));
  var state = {};
  if (manifest.stateFileId) {
    try { state = JSON.parse(DriveApp.getFileById(String(manifest.stateFileId)).getBlob().getDataAsString('UTF-8')); }
    catch (ignored) { state = {}; }
  }
  return {manifest: manifest, state: state};
}

function queueDisposition_(snapshot) {
  var state = snapshot && snapshot.state || {};
  var entries = Array.isArray(state.entries) ? state.entries : [];
  var last = String(state.lastRunStatus || '');
  // Older failed uploads can have no entry rows at all.  Their terminal
  // lastRunStatus must be honored before the empty-state fallback, otherwise
  // they stay at the head of the active queue forever and intercept every new
  // upload.
  if (['NEEDS_USER_REVIEW', 'NO_SUPPORTED_MODEL', 'ERROR'].indexOf(last) >= 0) return 'review';
  if (last === 'COMPLETE') return 'complete';
  if (!entries.length) return 'active';
  var completed = entries.every(function(item) { return ['COMPLETE', 'SKIPPED'].indexOf(String(item.status || '')) >= 0; });
  if (completed || last === 'COMPLETE') return 'complete';
  var terminal = entries.every(function(item) {
    return ['COMPLETE', 'SKIPPED', 'NOT_FOUND', 'AMBIGUOUS', 'NEEDS_METADATA_REVIEW', 'ERROR'].indexOf(String(item.status || '')) >= 0;
  });
  if (terminal || ['NEEDS_USER_REVIEW', 'NO_SUPPORTED_MODEL', 'ERROR'].indexOf(last) >= 0) return 'review';
  return 'active';
}

function repairQueueLists_() {
  var properties = PropertiesService.getScriptProperties();
  var active = queueIds_('PRIVATE_QUEUE_MANIFEST_IDS').filter(function(value, index, values) { return values.indexOf(value) === index; });
  var reviews = queueIds_('PRIVATE_REVIEW_MANIFEST_IDS').filter(function(value, index, values) { return values.indexOf(value) === index; });
  var kept = [];
  active.forEach(function(id) {
    try {
      var disposition = queueDisposition_(queueManifestSnapshot_(id));
      if (disposition === 'active') kept.push(id);
      else if (disposition === 'review' && reviews.indexOf(id) < 0) reviews.push(id);
    } catch (error) {
      // A temporary Drive read failure must never discard a private upload.
      kept.push(id);
    }
  });
  reviews = reviews.filter(function(id) { return kept.indexOf(id) < 0; }).slice(-200);
  properties.setProperty('PRIVATE_QUEUE_MANIFEST_IDS', JSON.stringify(kept.slice(-200)));
  properties.setProperty('PRIVATE_REVIEW_MANIFEST_IDS', JSON.stringify(reviews));
  var current = String(properties.getProperty('CURRENT_QUEUE_MANIFEST_ID') || '');
  if (current && kept.indexOf(current) < 0) properties.deleteProperty('CURRENT_QUEUE_MANIFEST_ID');
  return {active: kept.slice(-200), review: reviews};
}

function placeQueueResult_(properties, id, status, allTargetsComplete) {
  var active = queueIds_('PRIVATE_QUEUE_MANIFEST_IDS').filter(function(item) { return item !== id; });
  var reviews = queueIds_('PRIVATE_REVIEW_MANIFEST_IDS').filter(function(item) { return item !== id; });
  var terminalReview = ['NEEDS_USER_REVIEW', 'NO_SUPPORTED_MODEL', 'ERROR'].indexOf(status) >= 0;
  if (!allTargetsComplete && !terminalReview) active.push(id);
  if (terminalReview) reviews.push(id);
  properties.setProperty('PRIVATE_QUEUE_MANIFEST_IDS', JSON.stringify(active.filter(function(value, index, values) { return values.indexOf(value) === index; }).slice(-200)));
  properties.setProperty('PRIVATE_REVIEW_MANIFEST_IDS', JSON.stringify(reviews.filter(function(value, index, values) { return values.indexOf(value) === index; }).slice(-200)));
  if (String(properties.getProperty('CURRENT_QUEUE_MANIFEST_ID') || '') === id && (allTargetsComplete || terminalReview)) {
    properties.deleteProperty('CURRENT_QUEUE_MANIFEST_ID');
  }
}

function queueViewManifestId_() {
  var properties = PropertiesService.getScriptProperties();
  var active = queueIds_('PRIVATE_QUEUE_MANIFEST_IDS');
  var reviews = queueIds_('PRIVATE_REVIEW_MANIFEST_IDS');
  var current = String(properties.getProperty('CURRENT_QUEUE_MANIFEST_ID') || '');
  if (current && active.indexOf(current) >= 0) return current;
  var latest = String(properties.getProperty('LATEST_QUEUE_MANIFEST_ID') || '');
  if (latest && active.concat(reviews).indexOf(latest) >= 0) return latest;
  return active.length ? active[0] : (reviews.length ? reviews[reviews.length - 1] : '');
}

function handlePrivateQueue_(body) {
  assertCallbackSecret_(body);
  var properties = PropertiesService.getScriptProperties();
  var incomingEmail = body.serviceAccountEmail ? normalizeServiceAccountEmail_(body.serviceAccountEmail) : '';
  var storedEmail = String(properties.getProperty('SERVICE_ACCOUNT_EMAIL') || '').trim().toLowerCase();
  var serviceAccountEmail = incomingEmail || normalizeServiceAccountEmail_(storedEmail);
  if (incomingEmail && incomingEmail !== storedEmail) {
    properties.setProperty('SERVICE_ACCOUNT_EMAIL', serviceAccountEmail);
    driveEnsureEditor_(ensureManagementFolderId_(), serviceAccountEmail);
  }
  // Scheduled queue checks also scan the connected books-json inbox. Direct
  // uploads pass their manifest id straight to Actions and avoid this scan.
  importCanonicalFolder_({limit: 10});
  var queueLists = repairQueueLists_();
  var ids = queueLists.active;
  if (incomingEmail && incomingEmail !== storedEmail) {
    if (ids.length) ensureQueueManifestAccess_(ids[0], serviceAccountEmail);
  }
  var manifestId = ids.length ? ids[0] : '';
  if (manifestId) properties.setProperty('CURRENT_QUEUE_MANIFEST_ID', manifestId);
  else properties.deleteProperty('CURRENT_QUEUE_MANIFEST_ID');
  return json_({ok: true, manifestId: manifestId});
}

function normalizeServiceAccountEmail_(value) {
  var email = String(value || '').trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.iam\.gserviceaccount\.com$/i.test(email)) {
    throw new Error('GitHub에 저장된 서비스 계정 정보가 올바르지 않습니다.');
  }
  return email;
}

function ensureQueueManifestAccess_(manifestId, serviceAccountEmail) {
  try {
    var metadata = Drive.Files.get(String(manifestId), {fields: 'id,parents', supportsAllDrives: true});
    // Share the manifest itself as well as its session folder. The direct
    // permission makes the item readable immediately while the folder
    // permission covers the uploaded parts and state files created later.
    driveEnsureEditor_(String(manifestId), serviceAccountEmail);
    (metadata.parents || []).forEach(function(parentId) {
      driveEnsureEditor_(parentId, serviceAccountEmail);
    });
  } catch (error) {
    throw driveAdvancedError_('비공개 대기열을 서비스 계정과 공유하지 못했습니다', error);
  }
}

function handleQueueState_(body) {
  assertCallbackSecret_(body);
  var id = String(body.manifestId || '');
  if (!/^[A-Za-z0-9_-]{10,200}$/.test(id)) throw new Error('대기열 ID가 올바르지 않습니다.');
  var allowed = queueIds_('PRIVATE_QUEUE_MANIFEST_IDS').concat(queueIds_('PRIVATE_REVIEW_MANIFEST_IDS'));
  if (allowed.indexOf(id) < 0) throw new Error('등록되지 않은 대기열입니다.');
  var state = body.state;
  if (body.stateGzipBase64) {
    var zipped = Utilities.newBlob(Utilities.base64Decode(String(body.stateGzipBase64)), 'application/gzip');
    state = JSON.parse(Utilities.ungzip(zipped).getDataAsString('UTF-8'));
  }
  if (!state || typeof state !== 'object' || Array.isArray(state)) throw new Error('대기열 상태 형식이 올바르지 않습니다.');
  var encoded = JSON.stringify(state, null, 2);
  if (encoded.length > 10000000) throw new Error('대기열 상태가 너무 큽니다.');
  var manifestFile = DriveApp.getFileById(id);
  var manifest = JSON.parse(manifestFile.getBlob().getDataAsString('UTF-8'));
  var stateFileId = String(manifest.stateFileId || '');
  if (!stateFileId) {
    var created = driveCreateFile_(
      {name: 'queue-state.json', parents: [String(manifest.sessionFolderId || '')]},
      Utilities.newBlob('{}').getBytes(),
      'application/json'
    );
    stateFileId = created.id;
    manifest.stateFileId = stateFileId;
    driveUpdateFileContent_(id, JSON.stringify(manifest, null, 2), 'application/json');
  }
  driveUpdateFileContent_(stateFileId, encoded, 'application/json');
  return json_({ok: true, stateFileId: stateFileId});
}

function handleQueueResult_(body) {
  assertCallbackSecret_(body);
  var id = String(body.manifestId || '');
  var status = String(body.status || '');
  var resultId = String(body.resultId || '');
  if (!/^[0-9a-f]{64}$/.test(resultId)) throw new Error('대기열 결과 번호가 올바르지 않습니다.');
  var allTargetsComplete = status === 'COMPLETE' && body.allTargetsComplete === true;
  var manifestKind = '';
  try { manifestKind = String(JSON.parse(DriveApp.getFileById(id).getBlob().getDataAsString('UTF-8')).kind || ''); } catch (ignored) {}
  var lock = LockService.getScriptLock();
  lock.waitLock(15000);
  try {
  var properties = PropertiesService.getScriptProperties();
  var resultKey = 'QUEUE_RESULT_' + resultId;
  var previous = properties.getProperty(resultKey);
  // Repair queue placement even for an idempotent callback. Older deployments
  // could save the result marker while leaving a terminal manifest active.
  placeQueueResult_(properties, id, status, allTargetsComplete);
  if (previous) {
    var savedResponse = JSON.parse(previous);
    var previousTerminal = allTargetsComplete || ['NEEDS_USER_REVIEW', 'NO_SUPPORTED_MODEL', 'ERROR'].indexOf(status) >= 0;
    var previousCanContinue = manifestKind !== 'content-edit' &&
      (status === 'PAUSED_SAFETY_BUDGET' || previousTerminal) &&
      queueIds_('PRIVATE_QUEUE_MANIFEST_IDS').length > 0;
    // A worker may repeat an already accepted result callback after a network
    // timeout.  Queue placement is idempotent, but the older implementation
    // returned here and could strand the next upload until the 20-minute cron.
    if (previousCanContinue && !savedResponse.continued) {
      try {
        dispatchQueueWorkflow_();
        savedResponse.continued = true;
        properties.setProperty(resultKey, JSON.stringify(savedResponse));
      } catch (ignored) {}
    }
    return json_(savedResponse);
  }
  if (manifestKind === 'content-edit') {
    var currentSave = contentSaveStatus_();
    if (!currentSave.manifestId || currentSave.manifestId === id) {
      properties.setProperty('CONTENT_SAVE_STATUS', JSON.stringify({
        requestId: currentSave.requestId || '', manifestId: id,
        status: allTargetsComplete ? 'COMPLETE' : (status === 'ERROR' ? 'ERROR' : status),
        updatedAt: new Date().toISOString(),
        error: status === 'ERROR' ? String((body.summary || {}).message || 'GitHub Actions가 편집 내용을 저장하지 못했습니다.') : ''
      }));
    }
  }
  if (allTargetsComplete && manifestKind !== 'content-edit') {
    var summary = body.summary || {};
    var recipient = properties.getProperty('COMPLETION_EMAIL') || 'narepheus@gmail.com';
    sendEmail_(recipient, '[서재지도] 업로드 대기열 처리 완료', [
      '업로드 대기열의 모든 대상 처리가 끝났습니다.',
      '전체: ' + (summary.totalFiles || 0),
      '완료: ' + (summary.complete || 0),
      '건너뜀: ' + (summary.skipped || 0),
      '실패: ' + (summary.failed || 0)
    ].join('\n'));
  }
  var terminalForQueue = allTargetsComplete || ['NEEDS_USER_REVIEW', 'NO_SUPPORTED_MODEL', 'ERROR'].indexOf(status) >= 0;
  var continueNow = manifestKind !== 'content-edit' && (status === 'PAUSED_SAFETY_BUDGET' || (terminalForQueue && queueIds_('PRIVATE_QUEUE_MANIFEST_IDS').length > 0));
  var continued = false;
  if (continueNow) {
    try { dispatchQueueWorkflow_(); continued = true; } catch (error) { continued = false; }
  }
  var response = {ok: true, continued: continued};
  properties.setProperty(resultKey, JSON.stringify(response));
  return json_(response);
  } finally {
    lock.releaseLock();
  }
}

function queueAdmin_(options) {
  options = options || {};
  var wanted = String(options.status || '');
  var selectedId = String(options.manifestId || '');
  if (selectedId && !/^[A-Za-z0-9_-]{10,200}$/.test(selectedId)) throw new Error('대기열 ID가 올바르지 않습니다.');
  var page = Math.max(1, Number(options.page) || 1);
  var pageSize = Math.min(200, Math.max(20, Number(options.pageSize) || 200));
  var seen = 0;
  var lists = repairQueueLists_();
  var activeMap = {};
  var reviewMap = {};
  lists.active.forEach(function(id) { activeMap[id] = true; });
  lists.review.forEach(function(id) { reviewMap[id] = true; });
  var ids = lists.active.slice().reverse().concat(lists.review.slice().reverse()).filter(function(value, index, values) { return values.indexOf(value) === index; });
  if (selectedId) ids = ids.indexOf(selectedId) >= 0 ? [selectedId] : [];
  return ids.slice(0, 100).map(function(id) {
    try {
      var snapshot = queueManifestSnapshot_(id);
      var manifest = snapshot.manifest;
      var state = snapshot.state;
      var entries = [];
      if (manifest.stateFileId && Array.isArray(state.entries)) {
        entries = (state.entries || []).filter(function(item) {
          if (wanted && item.status !== wanted) return false;
          // Review history can contain dozens of already completed rows. The
          // default view shows only rows that still need an action.
          if (!wanted && reviewMap[id] && ['COMPLETE', 'SKIPPED'].indexOf(String(item.status || '')) >= 0) return false;
          var include = seen >= (page - 1) * pageSize && seen < page * pageSize;
          seen++;
          return include;
        }).map(function(item) {
          return {queueIndex: item.queueIndex, filename: String(item.filename || '').slice(0, 300), relativePath: String(item.relativePath || '').slice(0, 1000), status: item.status, reason: String(item.reason || '').slice(0, 300), bookId: item.bookId || ''};
        });
      }
      return {
        manifestId: id, kind: manifest.kind, originalFilename: manifest.originalFilename,
        createdAt: manifest.createdAt, scope: activeMap[id] ? 'active' : 'review',
        lastRunStatus: String(state.lastRunStatus || ''), entries: entries
      };
    } catch (error) {
      return {manifestId: id, kind: 'unknown', originalFilename: '', entries: [], error: '관리 파일을 읽지 못했습니다.'};
    }
  });
}

function retryQueue_(body) {
  var id = String(body.manifestId || '');
  if (!/^[A-Za-z0-9_-]{10,200}$/.test(id)) throw new Error('대기열 ID가 올바르지 않습니다.');
  var snapshot = queueManifestSnapshot_(id);
  var state = snapshot.state || {};
  if (Array.isArray(state.entries)) {
    state.entries.forEach(function(item) {
      if (['ERROR', 'NEEDS_METADATA_REVIEW'].indexOf(String(item.status || '')) < 0) return;
      item.status = item.driveFileId ? 'MATCHED' : 'NOT_FOUND';
      item.reason = '사용자가 다시 처리를 요청했습니다.';
    });
    state.lastRunStatus = '';
    state.updatedAt = new Date().toISOString();
    if (snapshot.manifest.stateFileId) driveUpdateFileContent_(snapshot.manifest.stateFileId, JSON.stringify(state, null, 2), 'application/json');
  }
  PropertiesService.getScriptProperties().setProperty('PRIVATE_REVIEW_MANIFEST_IDS', JSON.stringify(queueIds_('PRIVATE_REVIEW_MANIFEST_IDS').filter(function(item) { return item !== id; })));
  appendQueueManifest_(id);
  return {ok: true, dispatch: dispatchQueueWorkflow_(id)};
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

function githubTokenLooksValid_(token) {
  return /^(github_pat_|ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_]{20,300}$/.test(String(token || '').trim());
}

function githubDispatchStatus_() {
  var properties = PropertiesService.getScriptProperties();
  var token = String(properties.getProperty('GITHUB_TOKEN') || '').trim();
  return {
    configured: Boolean(token),
    formatValid: githubTokenLooksValid_(token),
    masked: token ? '••••' + token.slice(-4) : '',
    updatedAt: properties.getProperty('GITHUB_TOKEN_UPDATED_AT') || '',
    verifiedAt: properties.getProperty('GITHUB_TOKEN_VERIFIED_AT') || '',
    lastCode: Number(properties.getProperty('GITHUB_DISPATCH_LAST_CODE') || 0),
    lastReason: properties.getProperty('GITHUB_DISPATCH_LAST_REASON') || ''
  };
}

function verifyGitHubContentsWrite_(owner, repo, token) {
  var url = 'https://api.github.com/repos/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo);
  var response = UrlFetchApp.fetch(url, {headers: {Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'}, muteHttpExceptions: true});
  if (response.getResponseCode() !== 200) throw new Error('GitHub 저장소 권한 확인 실패: HTTP ' + response.getResponseCode());
  var info = JSON.parse(response.getContentText() || '{}');
  if (!info.permissions || info.permissions.push !== true) throw new Error('GitHub 토큰에 Contents: Read and write 권한이 없습니다. 이 권한을 추가한 토큰을 입력하세요.');
}

function updateGitHubToken_(body) {
  var token = String(body.githubToken || '').trim();
  if (!githubTokenLooksValid_(token)) {
    throw new Error('GitHub 토큰 형식이 올바르지 않습니다. 복사할 때 앞뒤 공백이나 URL이 함께 들어가지 않았는지 확인하세요.');
  }
  var owner = requiredProperty_('GITHUB_OWNER');
  var repo = requiredProperty_('GITHUB_REPO');
  verifyGitHubContentsWrite_(owner, repo, token);
  var result = requestQueueWorkflow_(owner, repo, token);
  if (!result.ok) throw new Error(githubDispatchFailureMessage_(result.code, result.reason));
  var now = new Date().toISOString();
  PropertiesService.getScriptProperties().setProperties({
    GITHUB_TOKEN: token,
    GITHUB_TOKEN_UPDATED_AT: now,
    GITHUB_TOKEN_VERIFIED_AT: now,
    GITHUB_DISPATCH_LAST_CODE: String(result.code),
    GITHUB_DISPATCH_LAST_REASON: ''
  });
  return {ok: true, githubDispatch: githubDispatchStatus_(), dispatch: {requested: true, scheduledFallback: false}};
}

function updateMetadata_(body) {
  var driveFileId = String(body.driveFileId || '').trim();
  var bookId = String(body.bookId || '').trim();
  if (!/^[A-Za-z0-9_-]{8,100}$/.test(bookId)) throw new Error('도서 식별번호가 올바르지 않습니다.');
  if (driveFileId) {
    if (!/^[A-Za-z0-9_-]{10,200}$/.test(driveFileId)) throw new Error('원본 파일 ID가 올바르지 않습니다.');
    assertFileWithinRoot_(driveFileId);
  }
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
  document.byBookId = document.byBookId || {};
  document.byBookId[bookId] = override;
  if (driveFileId) document.byDriveFileId[driveFileId] = override;
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

function updateBookContent_(body) {
  var driveFileId = String(body.driveFileId || '').trim();
  var bookId = String(body.bookId || '').trim();
  if (!/^[A-Za-z0-9_-]{8,100}$/.test(bookId)) throw new Error('도서 식별번호가 올바르지 않습니다.');
  if (driveFileId) {
    if (!/^[A-Za-z0-9_-]{10,200}$/.test(driveFileId)) throw new Error('원본 파일 ID가 올바르지 않습니다.');
    assertFileWithinRoot_(driveFileId);
  }
  if (!body.content || typeof body.content !== 'object' || Array.isArray(body.content)) throw new Error('저장할 인덱싱 내용이 올바르지 않습니다.');
  var content = JSON.parse(JSON.stringify(body.content));
  validatePublicBookContent_(content, 0);
  var serialized = JSON.stringify(content);
  if (serialized.length > 1500000) throw new Error('편집 내용이 너무 큽니다. 150만 자 이하로 저장하세요.');

  var owner = requiredProperty_('GITHUB_OWNER');
  var repo = requiredProperty_('GITHUB_REPO');
  var token = requiredProperty_('GITHUB_TOKEN');
  var path = 'data/content-overrides.json';
  var url = 'https://api.github.com/repos/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo) + '/contents/' + path;
  var headers = {Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'};
  var currentResponse = UrlFetchApp.fetch(url + '?ref=main', {headers: headers, muteHttpExceptions: true});
  var currentFile = null;
  var document = {schemaVersion: 1, description: '도서별 인덱싱 내용 수동 수정값. 원문 연결을 바꿔도 보존됩니다.', byDriveFileId: {}, byBookId: {}};
  if (currentResponse.getResponseCode() === 200) {
    currentFile = JSON.parse(currentResponse.getContentText() || '{}');
    var decoded = Utilities.newBlob(Utilities.base64Decode(String(currentFile.content || '').replace(/\s/g, ''))).getDataAsString('UTF-8');
    document = JSON.parse(decoded || '{}');
    document.byDriveFileId = document.byDriveFileId || {};
    document.byBookId = document.byBookId || {};
  } else if (currentResponse.getResponseCode() !== 404) {
    throw new Error('기존 내용 수정값을 읽지 못했습니다: HTTP ' + currentResponse.getResponseCode());
  }
  var updatedAt = new Date().toISOString();
  var override = {content: content, updatedAt: updatedAt};
  document.byBookId[bookId] = override;
  if (driveFileId) document.byDriveFileId[driveFileId] = override;
  var encoded = Utilities.base64Encode(Utilities.newBlob(JSON.stringify(document, null, 2) + '\n', 'application/json', 'content-overrides.json').getBytes());
  var payload = {message: 'Update indexed book content', content: encoded, branch: 'main'};
  if (currentFile && currentFile.sha) payload.sha = currentFile.sha;
  var updateResponse = UrlFetchApp.fetch(url, {method: 'put', contentType: 'application/json', headers: headers, muteHttpExceptions: true, payload: JSON.stringify(payload)});
  var code = updateResponse.getResponseCode();
  if (code !== 200 && code !== 201) throw new Error('인덱싱 내용 저장에 실패했습니다: HTTP ' + code);
  var result = JSON.parse(updateResponse.getContentText() || '{}');
  return {ok: true, updatedAt: updatedAt, commitUrl: result.commit && result.commit.html_url || ''};
}

function deleteBooks_(body) {
  var books = Array.isArray(body.books) ? body.books : [];
  if (!books.length || books.length > 100) throw new Error('한 번에 삭제할 도서는 1권 이상 100권 이하여야 합니다.');
  var normalized = books.map(function(item) {
    var bookId = String(item && item.bookId || '').trim();
    var driveFileId = String(item && item.driveFileId || '').trim();
    if (!/^[A-Za-z0-9_-]{8,100}$/.test(bookId) || !/^[A-Za-z0-9_-]{10,200}$/.test(driveFileId)) throw new Error('삭제할 도서 정보가 올바르지 않습니다.');
    assertFileWithinRoot_(driveFileId);
    return {bookId: bookId, driveFileId: driveFileId, title: String(item && item.title || '').trim().slice(0, 300)};
  });
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var owner = requiredProperty_('GITHUB_OWNER');
    var repo = requiredProperty_('GITHUB_REPO');
    var token = requiredProperty_('GITHUB_TOKEN');
    var path = 'data/deleted-books.json';
    var url = 'https://api.github.com/repos/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo) + '/contents/' + path;
    var headers = {Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'};
    var currentResponse = UrlFetchApp.fetch(url + '?ref=main', {headers: headers, muteHttpExceptions: true});
    var currentFile = null;
    var document = {schemaVersion: 1, description: '전체 도서 목록에서 숨긴 도서. Google Drive 원문과 분석 파일은 삭제하지 않습니다.', byDriveFileId: {}};
    if (currentResponse.getResponseCode() === 200) {
      currentFile = JSON.parse(currentResponse.getContentText() || '{}');
      var decoded = Utilities.newBlob(Utilities.base64Decode(String(currentFile.content || '').replace(/\s/g, ''))).getDataAsString('UTF-8');
      document = JSON.parse(decoded || '{}');
      document.byDriveFileId = document.byDriveFileId || {};
    } else if (currentResponse.getResponseCode() !== 404) {
      throw new Error('기존 삭제 목록을 읽지 못했습니다: HTTP ' + currentResponse.getResponseCode());
    }
    var deletedAt = new Date().toISOString();
    normalized.forEach(function(item) { document.byDriveFileId[item.driveFileId] = {bookId: item.bookId, title: item.title, deletedAt: deletedAt}; });
    var encoded = Utilities.base64Encode(Utilities.newBlob(JSON.stringify(document, null, 2) + '\n', 'application/json', 'deleted-books.json').getBytes());
    var payload = {message: 'Hide selected books from library', content: encoded, branch: 'main'};
    if (currentFile && currentFile.sha) payload.sha = currentFile.sha;
    var updateResponse = UrlFetchApp.fetch(url, {method: 'put', contentType: 'application/json', headers: headers, muteHttpExceptions: true, payload: JSON.stringify(payload)});
    var code = updateResponse.getResponseCode();
    if (code !== 200 && code !== 201) throw new Error('도서 삭제 목록 저장에 실패했습니다: HTTP ' + code);
    return {ok: true, deleted: normalized, deletedAt: deletedAt};
  } finally {
    lock.releaseLock();
  }
}

function handleBookContentUpdate_(body) {
  var requestId = String(body.requestId || '').trim();
  if (!/^[A-Za-z0-9-]{12,100}$/.test(requestId)) throw new Error('저장 요청 번호가 올바르지 않습니다.');
  var key = 'CONTENT_SAVE_STATUS';
  PropertiesService.getScriptProperties().setProperty(key, JSON.stringify({requestId: requestId, status: 'RUNNING', updatedAt: new Date().toISOString()}));
  var directError = '';
  try {
    var lock = LockService.getScriptLock();
    var locked = false;
    try {
      lock.waitLock(20000);
      locked = true;
      var direct = updateBookContent_(body);
      PropertiesService.getScriptProperties().setProperty(key, JSON.stringify({requestId: requestId, status: 'COMPLETE', updatedAt: direct.updatedAt || new Date().toISOString(), commitUrl: direct.commitUrl || ''}));
      return {ok: true, direct: true, updatedAt: direct.updatedAt, commitUrl: direct.commitUrl || ''};
    } catch (error) {
      directError = String(error && error.message || error);
    } finally {
      if (locked) lock.releaseLock();
    }
    var result = queueBookContentUpdate_(body);
    PropertiesService.getScriptProperties().setProperty(key, JSON.stringify({requestId: requestId, manifestId: result.manifestId || '', status: 'QUEUED', updatedAt: result.updatedAt || new Date().toISOString(), directFallbackReason: directError.slice(0, 300)}));
    result.directFallback = true;
    return result;
  } catch (error) {
    PropertiesService.getScriptProperties().setProperty(key, JSON.stringify({requestId: requestId, status: 'ERROR', updatedAt: new Date().toISOString(), error: String(error && error.message || error)}));
    throw error;
  }
}

function queueBookContentUpdate_(body) {
  var driveFileId = String(body.driveFileId || '').trim();
  var bookId = String(body.bookId || '').trim();
  if (!/^[A-Za-z0-9_-]{8,100}$/.test(bookId)) throw new Error('도서 식별번호가 올바르지 않습니다.');
  if (driveFileId) {
    if (!/^[A-Za-z0-9_-]{10,200}$/.test(driveFileId)) throw new Error('원본 파일 ID가 올바르지 않습니다.');
    assertFileWithinRoot_(driveFileId);
  }
  if (!body.content || typeof body.content !== 'object' || Array.isArray(body.content)) throw new Error('저장할 인덱싱 내용이 올바르지 않습니다.');
  var content = JSON.parse(JSON.stringify(body.content));
  validatePublicBookContent_(content, 0);
  var requestId = String(body.requestId || '').trim();
  var encoded = Utilities.newBlob(JSON.stringify({requestId: requestId, bookId: bookId, driveFileId: driveFileId, content: content}), 'application/json', 'content-edit.json').getBytes();
  if (encoded.length > 1500000) throw new Error('편집 내용이 너무 큽니다. 150만 바이트 이하로 저장하세요.');
  var partBytes = 131072;
  var partCount = Math.max(1, Math.ceil(encoded.length / partBytes));
  var started = startPrivateUpload_({requestId: requestId, kind: 'content-edit', filename: 'content-edit-' + requestId + '.json', size: encoded.length, partCount: partCount});
  for (var index = 0; index < partCount; index++) {
    savePrivateUploadPart_({uploadId: started.uploadId, uploadToken: started.uploadToken, index: index, base64: Utilities.base64Encode(encoded.slice(index * partBytes, Math.min(encoded.length, (index + 1) * partBytes)))});
  }
  var finished = finishPrivateUpload_({uploadId: started.uploadId, uploadToken: started.uploadToken, skipDispatch: true});
  var dispatch = dispatchContentEditWorkflow_(finished.manifestId);
  if (!dispatch.ok) throw new Error(githubDispatchFailureMessage_(dispatch.code, dispatch.reason));
  return {ok: true, queued: true, manifestId: finished.manifestId, updatedAt: new Date().toISOString()};
}

function dispatchContentEditWorkflow_(manifestId) {
  var owner = requiredProperty_('GITHUB_OWNER');
  var repo = requiredProperty_('GITHUB_REPO');
  var token = String(PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN') || '').trim();
  if (!githubTokenLooksValid_(token)) return {ok: false, code: 0, reason: 'missing_or_invalid_token'};
  var url = 'https://api.github.com/repos/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo) + '/actions/workflows/apply-content-edit.yml/dispatches';
  try {
    var response = UrlFetchApp.fetch(url, {
      method: 'post', contentType: 'application/json',
      payload: JSON.stringify({ref: 'main', inputs: {manifest_id: String(manifestId || '')}}),
      headers: {Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'},
      muteHttpExceptions: true
    });
    var code = response.getResponseCode();
    return {ok: code === 200 || code === 204, code: code, reason: code === 200 || code === 204 ? '' : 'github_http_' + code};
  } catch (error) {
    return {ok: false, code: 0, reason: 'github_request_unavailable'};
  }
}

function handleSourceLinkUpdate_(body) {
  var requestId = String(body.requestId || '').trim();
  var bookId = String(body.bookId || '').trim();
  var mode = String(body.sourceMode || '').trim();
  if (!/^[A-Za-z0-9-]{12,100}$/.test(requestId)) throw new Error('원문 연결 요청 번호가 올바르지 않습니다.');
  if (!/^[A-Za-z0-9_-]{8,100}$/.test(bookId)) throw new Error('도서 식별번호가 올바르지 않습니다.');
  if (mode !== 'drive' && mode !== 'none') throw new Error('원문 연결 방식을 선택하세요.');

  var source = {};
  if (mode === 'drive') {
    var driveFileId = String(body.driveFileId || '').trim();
    if (!/^[A-Za-z0-9_-]{10,200}$/.test(driveFileId)) throw new Error('선택한 원본 파일 ID가 올바르지 않습니다.');
    assertFileWithinRoot_(driveFileId);
    var metadata = Drive.Files.get(driveFileId, {
      fields: 'id,name,mimeType,size,modifiedTime,webViewLink,parents', supportsAllDrives: true
    });
    var filename = String(metadata.name || '');
    var mimeType = String(metadata.mimeType || '');
    if (mimeType !== 'text/plain' && mimeType !== 'application/epub+zip' && !/\.(txt|epub)$/i.test(filename)) {
      throw new Error('TXT 또는 EPUB 원본만 연결할 수 있습니다.');
    }
    source = {
      driveFileId: driveFileId, filename: filename, mimeType: mimeType,
      size: String(metadata.size || ''), modifiedTime: String(metadata.modifiedTime || ''),
      webViewLink: String(metadata.webViewLink || ''), parents: metadata.parents || []
    };
  }

  var value = {
    requestId: requestId, bookId: bookId,
    previousDriveFileId: String(body.previousDriveFileId || ''), sourceMode: mode, source: source
  };
  var encoded = Utilities.newBlob(JSON.stringify(value), 'application/json', 'source-link.json').getBytes();
  var partBytes = 131072;
  var partCount = Math.max(1, Math.ceil(encoded.length / partBytes));
  var started = startPrivateUpload_({
    requestId: requestId, kind: 'source-link', filename: 'source-link-' + requestId + '.json',
    size: encoded.length, partCount: partCount
  });
  for (var index = 0; index < partCount; index++) {
    savePrivateUploadPart_({
      uploadId: started.uploadId, uploadToken: started.uploadToken, index: index,
      base64: Utilities.base64Encode(encoded.slice(index * partBytes, Math.min(encoded.length, (index + 1) * partBytes)))
    });
  }
  var finished = finishPrivateUpload_({uploadId: started.uploadId, uploadToken: started.uploadToken, skipDispatch: true});
  var dispatch = dispatchSourceLinkWorkflow_(finished.manifestId);
  if (!dispatch.ok) throw new Error(githubDispatchFailureMessage_(dispatch.code, dispatch.reason));
  return {ok: true, queued: true, manifestId: finished.manifestId, requestId: requestId};
}

function dispatchSourceLinkWorkflow_(manifestId) {
  var owner = requiredProperty_('GITHUB_OWNER');
  var repo = requiredProperty_('GITHUB_REPO');
  var token = String(PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN') || '').trim();
  if (!githubTokenLooksValid_(token)) return {ok: false, code: 0, reason: 'missing_or_invalid_token'};
  var url = 'https://api.github.com/repos/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo) + '/actions/workflows/apply-source-link.yml/dispatches';
  try {
    var response = UrlFetchApp.fetch(url, {
      method: 'post', contentType: 'application/json',
      payload: JSON.stringify({ref: 'main', inputs: {manifest_id: String(manifestId || '')}}),
      headers: {Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'},
      muteHttpExceptions: true
    });
    var code = response.getResponseCode();
    return {ok: code === 200 || code === 204, code: code, reason: code === 200 || code === 204 ? '' : 'github_http_' + code};
  } catch (error) {
    return {ok: false, code: 0, reason: 'github_request_unavailable'};
  }
}

function contentSaveStatus_() {
  try {
    return JSON.parse(PropertiesService.getScriptProperties().getProperty('CONTENT_SAVE_STATUS') || '{}');
  } catch (error) {
    return {};
  }
}

function validatePublicBookContent_(value, depth) {
  if (depth > 30) throw new Error('편집 내용의 구조가 너무 깊습니다.');
  if (!value || typeof value !== 'object') return;
  Object.keys(value).forEach(function(key) {
    if (/^(?:source|raw|original|full)[_-]?text$/i.test(key)) throw new Error('원문 전문은 공개 저장소에 저장할 수 없습니다.');
    if (/(?:api[_-]?key|secret|access[_-]?token|refresh[_-]?token|password)/i.test(key)) throw new Error('API 키나 인증 정보는 공개 저장소에 저장할 수 없습니다.');
    validatePublicBookContent_(value[key], depth + 1);
  });
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
  var names = ['status','phase','message','model','folderId','folderName','runId','runUrl','queueManifestId','queueKind','uploadFilename','currentFileName','currentFileIndex','totalFiles',
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

function dispatchQueueWorkflow_(preferredManifestId) {
  var owner = requiredProperty_('GITHUB_OWNER');
  var repo = requiredProperty_('GITHUB_REPO');
  var properties = PropertiesService.getScriptProperties();
  var token = String(properties.getProperty('GITHUB_TOKEN') || '').trim();
  if (!githubTokenLooksValid_(token)) return queueScheduledFallback_('missing_or_invalid_token', 0);
  // The upload that just finished already knows its manifest id. Passing it to
  // Actions avoids a slow web-app round trip that previously scanned every
  // historical queue before the worker could even start.
  var active = queueIds_('PRIVATE_QUEUE_MANIFEST_IDS');
  var preferred = String(preferredManifestId || '');
  var nextManifestId = preferred && active.indexOf(preferred) >= 0 ? preferred : (active.length ? active[0] : '');
  var result = requestQueueWorkflow_(owner, repo, token, nextManifestId);
  if (!result.ok) return queueScheduledFallback_(result.reason, result.code);
  var nextKind = '';
  var nextFilename = '';
  if (nextManifestId) {
    try {
      var nextManifest = queueManifestSnapshot_(nextManifestId).manifest;
      nextKind = String(nextManifest.kind || '');
      nextFilename = String(nextManifest.originalFilename || '');
    } catch (ignored) {}
  }
  properties.setProperties({GITHUB_TOKEN_VERIFIED_AT: new Date().toISOString(), GITHUB_DISPATCH_LAST_CODE: String(result.code), GITHUB_DISPATCH_LAST_REASON: ''});
  properties.setProperty('LIVE_STATUS_JSON', JSON.stringify({
    status: 'QUEUED', phase: 'QUEUE_UPLOAD', message: nextFilename ? nextFilename + ' 처리를 요청했습니다.' : '비공개 업로드 대기열을 등록하고 즉시 자동 처리를 요청했습니다.',
    queueManifestId: nextManifestId, queueKind: nextKind, uploadFilename: nextFilename,
    currentFileName: '', currentFileIndex: 0, totalFiles: 0, complete: 0, skipped: 0, failed: 0, metadataReview: 0,
    updatedAt: new Date().toISOString()
  }));
  return {requested: true, scheduledFallback: false};
}

function requestQueueWorkflow_(owner, repo, token, manifestId) {
  var url = 'https://api.github.com/repos/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo) + '/actions/workflows/queue-worker.yml/dispatches';
  try {
    var response = UrlFetchApp.fetch(url, {
      method: 'post', contentType: 'application/json', payload: JSON.stringify({ref: 'main', inputs: {manifest_id: String(manifestId || '')}}),
      headers: {Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'}, muteHttpExceptions: true
    });
    var code = response.getResponseCode();
    return {ok: code === 200 || code === 204, code: code, reason: code === 200 || code === 204 ? '' : 'github_http_' + code};
  } catch (error) {
    return {ok: false, code: 0, reason: 'github_request_unavailable'};
  }
}

function githubDispatchFailureMessage_(code, reason) {
  if (reason === 'missing_or_invalid_token') return 'GitHub 즉시 실행 토큰이 없거나 형식이 잘못되었습니다.';
  if (Number(code) === 401) return 'GitHub 인증이 거부되었습니다(HTTP 401). 만료되었거나 잘못된 토큰입니다.';
  if (Number(code) === 403) return 'GitHub 실행 권한이 없습니다(HTTP 403). 이 저장소의 Actions 쓰기 권한이 필요합니다.';
  if (Number(code) === 404) return 'GitHub 저장소 또는 queue-worker.yml을 찾지 못했습니다(HTTP 404). 저장소 접근 범위를 확인하세요.';
  if (Number(code) === 422) return 'GitHub가 main 브랜치 실행 요청을 거부했습니다(HTTP 422).';
  if (reason === 'github_request_unavailable') return 'GitHub에 연결할 수 없습니다. 잠시 후 다시 시도하세요.';
  return 'GitHub 즉시 실행 요청이 실패했습니다' + (code ? '(HTTP ' + code + ')' : '') + '.';
}

function queueScheduledFallback_(reason, code) {
  var properties = PropertiesService.getScriptProperties();
  var detail = githubDispatchFailureMessage_(code, reason);
  var lists = repairQueueLists_();
  var nextManifestId = lists.active.length ? lists.active[0] : '';
  var nextKind = '';
  var nextFilename = '';
  if (nextManifestId) {
    try {
      var nextManifest = queueManifestSnapshot_(nextManifestId).manifest;
      nextKind = String(nextManifest.kind || '');
      nextFilename = String(nextManifest.originalFilename || '');
    } catch (ignored) {}
  }
  properties.setProperties({
    GITHUB_DISPATCH_LAST_CODE: String(Number(code) || 0),
    GITHUB_DISPATCH_LAST_REASON: String(reason || 'scheduled'),
    LIVE_STATUS_JSON: JSON.stringify({
    status: 'QUEUED', phase: 'QUEUE_UPLOAD',
    message: '업로드는 안전하게 보관됐지만 즉시 실행에 실패했습니다. ' + detail + ' 정기 자동 실행은 계속 대기합니다.',
    queueManifestId: nextManifestId, queueKind: nextKind, uploadFilename: nextFilename,
    currentFileName: '', currentFileIndex: 0, totalFiles: 0, complete: 0, skipped: 0, failed: 0, metadataReview: 0,
    updatedAt: new Date().toISOString()
    })
  });
  return {requested: false, scheduledFallback: true, reason: String(reason || 'scheduled'), code: Number(code) || 0, message: detail};
}

function driveAdvancedError_(prefix, error) {
  var detail = String(error && error.message || error || '').replace(/\s+/g, ' ').trim().slice(0, 500);
  return new Error(prefix + (detail ? ': ' + detail : ''));
}

function driveGetMetadata_(id) {
  try {
    return Drive.Files.get(String(id), {fields: 'id,name,mimeType,size', supportsAllDrives: true});
  } catch (error) {
    throw driveAdvancedError_('비공개 관리 파일을 확인하지 못했습니다', error);
  }
}

function driveListChildren_(parentId) {
  var q = "'" + String(parentId).replace(/'/g, "\\'") + "' in parents and trashed = false";
  try {
    var response = Drive.Files.list({
      q: q,
      fields: 'files(id,name,size,mimeType)',
      pageSize: 1000,
      supportsAllDrives: true,
      includeItemsFromAllDrives: true
    });
    return response.files || [];
  } catch (error) {
    throw driveAdvancedError_('비공개 업로드 조각을 확인하지 못했습니다', error);
  }
}

function driveCreateMetadata_(metadata) {
  try {
    return Drive.Files.create(metadata, null, {fields: 'id,name', supportsAllDrives: true});
  } catch (error) {
    throw driveAdvancedError_('비공개 관리 폴더를 만들지 못했습니다', error);
  }
}

function driveCreateFile_(metadata, bytes, mimeType) {
  try {
    var blob = Utilities.newBlob(bytes, mimeType, metadata.name || 'upload.bin');
    return Drive.Files.create(metadata, blob, {fields: 'id,name,size', supportsAllDrives: true});
  } catch (error) {
    throw driveAdvancedError_('비공개 관리 파일을 저장하지 못했습니다', error);
  }
}

function driveCopyFile_(sourceFileId, metadata) {
  try {
    return Drive.Files.copy(metadata, String(sourceFileId), {
      fields: 'id,name,size,modifiedTime', supportsAllDrives: true
    });
  } catch (error) {
    throw driveAdvancedError_('books-json 파일을 비공개 대기열로 복사하지 못했습니다', error);
  }
}

function driveUpdateFileContent_(fileId, text, mimeType) {
  try {
    var blob = Utilities.newBlob(String(text || ''), mimeType || 'application/octet-stream');
    return Drive.Files.update({}, String(fileId), blob, {
      fields: 'id,size,modifiedTime',
      supportsAllDrives: true
    });
  } catch (error) {
    throw driveAdvancedError_('비공개 대기열 상태를 저장하지 못했습니다', error);
  }
}

function driveShareEditor_(fileId, email) {
  try {
    Drive.Permissions.create(
      {type: 'user', role: 'writer', emailAddress: email},
      String(fileId),
      {supportsAllDrives: true, sendNotificationEmail: false, fields: 'id'}
    );
  } catch (error) {
    throw driveAdvancedError_('비공개 관리 폴더를 서비스 계정과 공유하지 못했습니다', error);
  }
}

function driveEnsureEditor_(fileId, email) {
  try {
    var permissions = Drive.Permissions.list(String(fileId), {
      fields: 'permissions(id,type,role,emailAddress)', supportsAllDrives: true
    }).permissions || [];
    var exists = permissions.some(function(permission) {
      return String(permission.emailAddress || '').toLowerCase() === String(email || '').toLowerCase() &&
        ['writer', 'owner', 'organizer', 'fileOrganizer'].indexOf(permission.role) >= 0;
    });
    if (!exists) driveShareEditor_(fileId, email);
  } catch (error) {
    throw driveAdvancedError_('비공개 관리 폴더 권한을 확인하지 못했습니다', error);
  }
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
  sendEmail_(recipient, subject, message);
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

function sendEmail_(recipient, subject, message) {
  var encodedSubject = Utilities.base64Encode(String(subject || ''), Utilities.Charset.UTF_8);
  var mime = [
    'To: ' + String(recipient || ''),
    'Subject: =?UTF-8?B?' + encodedSubject + '?=',
    'MIME-Version: 1.0',
    'Content-Type: text/plain; charset=UTF-8',
    '',
    String(message || '')
  ].join('\r\n');
  var raw = Utilities.base64EncodeWebSafe(mime, Utilities.Charset.UTF_8).replace(/=+$/, '');
  var response = UrlFetchApp.fetch('https://gmail.googleapis.com/gmail/v1/users/me/messages/send', {
    method: 'post',
    contentType: 'application/json',
    headers: {Authorization: 'Bearer ' + ScriptApp.getOAuthToken()},
    payload: JSON.stringify({raw: raw}),
    muteHttpExceptions: true
  });
  var code = response.getResponseCode();
  if (code < 200 || code >= 300) throw new Error('완료 메일 전송 실패: HTTP ' + code);
}
