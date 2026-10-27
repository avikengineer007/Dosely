# Third-Party Vendored Assets

This document tracks third-party frontend dependencies vendored into the local repository.

## 1. Three.js

- **Package**: `three.js`
- **Version**: `r128`
- **File**: [`static/vendor/three.min.js`](file:///static/vendor/three.min.js)
- **Source URL**: `https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js`
- **File Size**: 603,445 bytes
- **SHA-384 Integrity Hash**: `sha384-CI3ELBVUz9XQO+97x6nwMDPosPR5XvsxW2ua7N1Xeygeh1IxtgqtCkGfQY9WWdHu`
- **License**: MIT
- **Rationale**: Vendored locally to eliminate third-party CDN runtime dependencies, protect user privacy, and prevent CDN supply-chain or outage risks. Served locally from the same origin (`/static/vendor/three.min.js`) with Subresource Integrity verification (`integrity="sha384-..."`) and `crossorigin="anonymous"`.
