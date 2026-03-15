"""BitTorrent seeder — keeps a libtorrent session alive to seed generated torrents."""

import logging
import shutil
from pathlib import Path
from typing import Optional

import libtorrent as lt

logger = logging.getLogger(__name__)


class Seeder:
    """Manages a libtorrent session for seeding torrents."""

    def __init__(self, seeding_dir: str, listen_ports: tuple[int, int] = (6881, 6891)):
        self.seeding_dir = Path(seeding_dir)
        self.seeding_dir.mkdir(parents=True, exist_ok=True)
        self.listen_ports = listen_ports
        self.session: Optional[lt.session] = None
        self._handles: dict[str, lt.torrent_handle] = {}  # infohash -> handle
        self._torrent_files: dict[str, bytes] = {}  # infohash -> .torrent bytes

    def start(self):
        """Start the libtorrent session and load existing torrents."""
        settings = {
            'listen_interfaces': f'0.0.0.0:{self.listen_ports[0]}',
            'enable_dht': True,
            'enable_lsd': True,
            'enable_upnp': False,   # VPS, no UPnP
            'enable_natpmp': False,
            'alert_mask': (
                lt.alert.category_t.error_notification
                | lt.alert.category_t.status_notification
            ),
        }
        self.session = lt.session(settings)
        logger.info("libtorrent session started on port %d", self.listen_ports[0])

        # Load all existing torrents from seeding directory
        self._load_existing()

    def stop(self):
        """Stop the session gracefully."""
        if self.session:
            self.session.pause()
            logger.info("libtorrent session stopped (%d torrents)", len(self._handles))
            self.session = None
            self._handles.clear()
            self._torrent_files.clear()

    def _load_existing(self):
        """Scan seeding directory and load all saved torrents."""
        count = 0
        for cid_dir in self.seeding_dir.iterdir():
            if not cid_dir.is_dir():
                continue
            torrent_file = cid_dir / "torrent.dat"
            data_dir = cid_dir / "data"
            if torrent_file.exists() and data_dir.exists():
                try:
                    torrent_bytes = torrent_file.read_bytes()
                    self._add_to_session(torrent_bytes, data_dir)
                    count += 1
                except Exception as e:
                    logger.error("Failed to load torrent from %s: %s", cid_dir, e)
        logger.info("Loaded %d existing torrents for seeding", count)

    def _add_to_session(self, torrent_bytes: bytes, data_dir: Path) -> Optional[str]:
        """Add a torrent to the libtorrent session. Returns infohash."""
        if not self.session:
            return None

        ti = lt.torrent_info(lt.bdecode(torrent_bytes))
        infohash = str(ti.info_hash())

        if infohash in self._handles:
            logger.debug("Torrent %s already loaded", infohash)
            return infohash

        params = lt.add_torrent_params()
        params.ti = ti
        params.save_path = str(data_dir)
        params.flags |= lt.torrent_flags.seed_mode  # We generated the data, skip hash check

        handle = self.session.add_torrent(params)
        self._handles[infohash] = handle
        self._torrent_files[infohash] = torrent_bytes
        logger.info("Seeding torrent %s (%s)", ti.name(), infohash)
        return infohash

    def add_torrent(self, cid: str, torrent_bytes: bytes, content_dir: Path) -> Optional[str]:
        """Add a new torrent for seeding.

        Copies content from content_dir to the seeding directory,
        saves the .torrent file, and loads into the session.

        Handles file renaming for single-file torrents: libtorrent expects the
        file at ``save_path / torrent_name``, but the source file from IPFS may
        have a different name (e.g. the CID).  We parse the torrent metadata to
        determine expected file paths and rename accordingly.

        Args:
            cid: IPFS CID (used as directory name)
            torrent_bytes: The .torrent file bytes
            content_dir: Path to the directory containing the files to seed

        Returns:
            infohash string, or None on failure
        """
        cid_dir = self.seeding_dir / cid
        data_dir = cid_dir / "data"
        torrent_file = cid_dir / "torrent.dat"

        try:
            # If already seeding this CID, check whether the torrent changed
            if torrent_file.exists() and data_dir.exists():
                old_bytes = torrent_file.read_bytes()
                old_ti = lt.torrent_info(lt.bdecode(old_bytes))
                old_hash = str(old_ti.info_hash())
                if old_hash in self._handles:
                    self.session.remove_torrent(self._handles[old_hash])
                    del self._handles[old_hash]
                    self._torrent_files.pop(old_hash, None)
                shutil.rmtree(cid_dir, ignore_errors=True)

            # Set up seeding directory structure
            cid_dir.mkdir(parents=True, exist_ok=True)

            # Copy content into data subdirectory
            if content_dir.exists():
                shutil.copytree(content_dir, data_dir)

            # Parse torrent to figure out expected file layout
            ti = lt.torrent_info(lt.bdecode(torrent_bytes))
            fs = ti.files()

            if fs.num_files() == 1:
                # Single-file torrent: libtorrent expects the file at
                # data_dir / ti.name().  The actual file from IPFS likely
                # has a different name (the CID).
                expected_name = ti.name()
                expected_path = data_dir / expected_name

                if not expected_path.exists():
                    # Find the actual file and rename it
                    actual_files = [f for f in data_dir.iterdir() if f.is_file()]
                    if len(actual_files) == 1:
                        actual_files[0].rename(expected_path)
                        logger.debug(
                            "Renamed %s -> %s for single-file torrent",
                            actual_files[0].name, expected_name,
                        )
                    else:
                        logger.warning(
                            "Single-file torrent but found %d files in %s",
                            len(actual_files), data_dir,
                        )
            else:
                # Multi-file torrent: libtorrent expects files at
                # data_dir / ti.name() / <relative_path>.
                # create_torrent built the torrent from the directory contents
                # directly, so the files should be at data_dir/<filename>.
                # But libtorrent expects them under data_dir/<torrent_name>/<filename>.
                torrent_dir_name = ti.name()
                nested_dir = data_dir / torrent_dir_name

                if not nested_dir.exists():
                    # Move all files into a subdirectory named after the torrent
                    nested_dir.mkdir(parents=True, exist_ok=True)
                    for item in list(data_dir.iterdir()):
                        if item != nested_dir:
                            item.rename(nested_dir / item.name)
                    logger.debug(
                        "Moved content into %s/ for multi-file torrent",
                        torrent_dir_name,
                    )

            # Save .torrent file
            torrent_file.write_bytes(torrent_bytes)

            # Load into session
            return self._add_to_session(torrent_bytes, data_dir)

        except Exception as e:
            logger.error("Failed to add torrent for CID %s: %s", cid, e)
            return None

    def get_torrent_file(self, infohash: str) -> Optional[bytes]:
        """Get .torrent file bytes by infohash."""
        return self._torrent_files.get(infohash)

    def get_torrent_file_by_cid(self, cid: str) -> Optional[bytes]:
        """Get .torrent file bytes by CID (looks on disk)."""
        torrent_file = self.seeding_dir / cid / "torrent.dat"
        if torrent_file.exists():
            return torrent_file.read_bytes()
        return None

    def status(self) -> dict:
        """Get seeder status."""
        if not self.session:
            return {"running": False, "torrents": 0}

        stats = []
        for infohash, handle in self._handles.items():
            s = handle.status()
            stats.append({
                "infohash": infohash,
                "name": s.name,
                "num_peers": s.num_peers,
                "num_seeds": s.num_seeds,
                "upload_rate": s.upload_rate,
                "total_upload": s.total_upload,
                "state": str(s.state),
            })

        return {
            "running": True,
            "torrents": len(self._handles),
            "details": stats,
        }


# Global seeder instance
_seeder: Optional[Seeder] = None


def get_seeder() -> Optional[Seeder]:
    """Get the global seeder instance."""
    return _seeder


def init_seeder(seeding_dir: str) -> Seeder:
    """Initialize and start the global seeder."""
    global _seeder
    _seeder = Seeder(seeding_dir)
    _seeder.start()
    return _seeder


def stop_seeder():
    """Stop the global seeder."""
    global _seeder
    if _seeder:
        _seeder.stop()
        _seeder = None
