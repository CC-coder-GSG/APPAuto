// Compatibility shim — the implementation moved to entity-preview-modal.js.
// Kept so browsers with a cached older app.js (which still imports this path)
// don't 404 and crash the whole module graph on next page load.
// Once all clients pick up the new app.js this file can be removed.
import './entity-preview-modal.js';
