/* 로컬 서재 저장소.
 * 동기화(클라우드 드라이브)를 나중에 얹기 쉽도록 메타데이터(순수 JSON)와
 * EPUB 파일(Blob)을 별도 스토어에 보관한다. 표지 Blob은 크기가 작아 메타데이터에 포함한다. */
(() => {
  const DB_NAME = "librum";
  const DB_VERSION = 2;
  let databasePromise = null;

  function openDatabase() {
    if (databasePromise) return databasePromise;
    databasePromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains("books")) {
          database.createObjectStore("books", { keyPath: "id" });
        }
        if (!database.objectStoreNames.contains("files")) {
          database.createObjectStore("files", { keyPath: "id" });
        }
        // v2: 형광펜·메모 저장소. 책별 조회를 위해 bookId 인덱스를 둔다.
        if (!database.objectStoreNames.contains("notes")) {
          const notes = database.createObjectStore("notes", { keyPath: "id" });
          notes.createIndex("bookId", "bookId", { unique: false });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return databasePromise;
  }

  function runTransaction(storeNames, mode, run) {
    return openDatabase().then((database) => new Promise((resolve, reject) => {
      const transaction = database.transaction(storeNames, mode);
      const result = run(transaction);
      transaction.oncomplete = () => resolve(result.value);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    }));
  }

  function requestValue(store, method, ...args) {
    const holder = { value: undefined };
    const request = store[method](...args);
    request.onsuccess = () => { holder.value = request.result; };
    return holder;
  }

  window.LibrumLibrary = {
    async list() {
      const books = await runTransaction(["books"], "readonly", (transaction) =>
        requestValue(transaction.objectStore("books"), "getAll"));
      return (books || []).sort((a, b) => (b.lastOpenedAt || 0) - (a.lastOpenedAt || 0));
    },

    async get(id) {
      return runTransaction(["books"], "readonly", (transaction) =>
        requestValue(transaction.objectStore("books"), "get", id));
    },

    async getFile(id) {
      const record = await runTransaction(["files"], "readonly", (transaction) =>
        requestValue(transaction.objectStore("files"), "get", id));
      return record?.blob || null;
    },

    async add(meta, fileBlob) {
      navigator.storage?.persist?.().catch(() => {});
      return runTransaction(["books", "files"], "readwrite", (transaction) => {
        transaction.objectStore("books").put(meta);
        transaction.objectStore("files").put({ id: meta.id, blob: fileBlob });
        return { value: meta.id };
      });
    },

    async update(id, patch) {
      return runTransaction(["books"], "readwrite", (transaction) => {
        const store = transaction.objectStore("books");
        const request = store.get(id);
        request.onsuccess = () => {
          if (request.result) store.put({ ...request.result, ...patch, updatedAt: Date.now() });
        };
        return { value: undefined };
      });
    },

    async remove(id) {
      return runTransaction(["books", "files", "notes"], "readwrite", (transaction) => {
        transaction.objectStore("books").delete(id);
        transaction.objectStore("files").delete(id);
        const noteStore = transaction.objectStore("notes");
        const cursorRequest = noteStore.index("bookId").openCursor(IDBKeyRange.only(id));
        cursorRequest.onsuccess = () => {
          const cursor = cursorRequest.result;
          if (cursor) { noteStore.delete(cursor.primaryKey); cursor.continue(); }
        };
        return { value: undefined };
      });
    },

    // --- 형광펜·메모 ---
    async listNotes(bookId) {
      const notes = await runTransaction(["notes"], "readonly", (transaction) =>
        requestValue(transaction.objectStore("notes").index("bookId"), "getAll", IDBKeyRange.only(bookId)));
      return (notes || []).sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0));
    },

    async saveNote(note) {
      navigator.storage?.persist?.().catch(() => {});
      return runTransaction(["notes"], "readwrite", (transaction) => {
        transaction.objectStore("notes").put(note);
        return { value: note.id };
      });
    },

    async removeNote(id) {
      return runTransaction(["notes"], "readwrite", (transaction) => {
        transaction.objectStore("notes").delete(id);
        return { value: undefined };
      });
    },
  };
})();
