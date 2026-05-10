import json
import os
import re
import subprocess
from pathlib import Path, PureWindowsPath
from typing import List, Optional

from dotenv import load_dotenv
from sqlalchemy.engine import URL

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env", override=True)

APP_VERSION = "1.41.0"
SCHEMA_VERSION = 6

_VALID_ENVS = ("development", "test", "production")


def _detect_worktree() -> Optional[str]:
    try:
        git_dir = subprocess.check_output(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(_PROJECT_ROOT), stderr=subprocess.DEVNULL, text=True,
        ).strip()
        git_common = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(_PROJECT_ROOT), stderr=subprocess.DEVNULL, text=True,
        ).strip()
        git_dir_resolved = str(Path(git_dir).resolve())
        git_common_resolved = str(Path(git_common).resolve())
        if git_dir_resolved != git_common_resolved:
            return str(_PROJECT_ROOT)
    except Exception:
        pass
    return None


class Settings:
    def __init__(self):
        self.CODE_ROOT = Path(__file__).resolve().parent.parent.parent
        self.BASE_DIR = self.CODE_ROOT

        storage_env = os.getenv("VIOLET_STORAGE_ROOT", "").strip()
        if storage_env:
            self.STORAGE_ROOT = Path(storage_env)
        else:
            self.STORAGE_ROOT = self.BASE_DIR

        self.MEDIA_DIR = self.STORAGE_ROOT / "media"
        self.ORIGINAL_DIR = self.MEDIA_DIR / "original"
        self.THUMBNAIL_DIR = self.MEDIA_DIR / "thumbnails"
        self.CACHE_DIR = self.MEDIA_DIR / "cache"
        self.DATA_DIR = self.STORAGE_ROOT / "data"
        self.SETTINGS_FILE = self.DATA_DIR / "settings.json"

        self.ORIGINAL_DIR.mkdir(parents=True, exist_ok=True)
        self.THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)

        self.file_settings = self._load_file_settings()
        self.settings = self._get_default_settings()
        self.settings.update(self.file_settings)

        # Persist the generated secret_key immediately so that a manual edit
        # removing the key from settings.json does not silently rotate it on
        # the next restart and invalidate all existing JWT tokens.
        if "secret_key" not in self.file_settings:
            self.file_settings["secret_key"] = self.settings["secret_key"]
            try:
                with open(self.SETTINGS_FILE, 'w') as _f:
                    json.dump(self.settings, _f, indent=2)
            except Exception:
                pass 
        
    @property
    def DEBUG(self) -> bool:
        return os.getenv("BLOMBOORU_DEBUG", "false").lower() == "true"

    @property
    def VIOLET_ENV(self) -> str:
        val = os.getenv("VIOLET_ENV", "development").strip().lower()
        if val not in _VALID_ENVS:
            raise RuntimeError(
                f"Invalid VIOLET_ENV={val!r}. Must be one of {_VALID_ENVS}."
            )
        return val

    @property
    def IS_TEST_ENV(self) -> bool:
        return self.VIOLET_ENV == "test"

    @property
    def IS_PRODUCTION_ENV(self) -> bool:
        return self.VIOLET_ENV == "production"

    @property
    def STORAGE_ROOT_EXPLICITLY_SET(self) -> bool:
        return bool(os.getenv("VIOLET_STORAGE_ROOT", "").strip())

    @property
    def WORKTREE_PATH(self) -> Optional[str]:
        return _detect_worktree()

    @property
    def VIOLET_TEST_FIXTURE_PATH(self) -> Optional[Path]:
        val = os.getenv("VIOLET_TEST_FIXTURE_PATH", "").strip()
        if val:
            return Path(val)
        return None

    @property
    def VIOLET_TEST_STORAGE_ROOT(self) -> Optional[Path]:
        val = os.getenv("VIOLET_TEST_STORAGE_ROOT", "").strip()
        if val:
            return Path(val)
        return None

    @property
    def TEST_STORAGE_ROOT_EXPLICITLY_SET(self) -> bool:
        return bool(os.getenv("VIOLET_TEST_STORAGE_ROOT", "").strip())

    def resolve_storage_path(self, stored_path: str) -> Optional[Path]:
        """Resolve a DB-stored relative path against STORAGE_ROOT.

        Returns None for empty, absolute, or traversal paths.
        """
        if not stored_path:
            return None
        raw = stored_path
        if raw.startswith("\\\\") or raw.startswith("//"):
            return None
        normalized = raw.replace("\\", "/")
        if normalized.startswith("/"):
            return None
        if re.match(r"^[A-Za-z]:", normalized):
            return None
        if PureWindowsPath(raw).is_absolute():
            return None
        probe = Path(normalized)
        if probe.is_absolute():
            return None
        if ".." in probe.parts:
            return None
        storage_resolved = self.STORAGE_ROOT.resolve()
        resolved = (storage_resolved / normalized).resolve()
        try:
            resolved.relative_to(storage_resolved)
        except ValueError:
            return None
        return resolved

    def storage_relative_path(self, absolute_path: Path) -> str:
        """Return POSIX-style relative path from STORAGE_ROOT for DB storage.

        Raises ValueError if the path is not under STORAGE_ROOT.
        """
        resolved = absolute_path.resolve()
        storage_resolved = self.STORAGE_ROOT.resolve()
        rel = resolved.relative_to(storage_resolved)
        return str(rel).replace("\\", "/")

    def _load_file_settings(self) -> dict:
        if self.SETTINGS_FILE.exists():
            with open(self.SETTINGS_FILE, 'r') as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return {}
        return {}
        
    def _get_default_settings(self) -> dict:
        return {
            "app_name": "V.I.O.L.E.T.",
            "first_run": True,
            "database": {
                "host": "db",
                "port": 5432,
                "name": "blombooru",
                "user": "postgres",
                "password": ""
            },
            "redis": {
                "host": "redis",
                "port": 6379,
                "db": 0,
                "password": "",
                "enabled": False
            },
            "shared_tags": {
                "enabled": False,
                "host": "shared-tag-db",
                "port": 5432,
                "name": "shared_tags",
                "user": "postgres",
                "password": ""
            },
            "items_per_page": 64,
            "default_sort": "uploaded_at",
            "default_order": "desc",
            "sidebar_filter_mode": "rating",
            "sidebar_custom_buttons": [],
            "media_type_tags": {"image": [], "gif": [], "video": []},
            "secret_key": os.urandom(32).hex()
        }
    
    def get_items_per_page(self) -> int:
        """Get items per page setting"""
        return self.settings.get("items_per_page", 64)
    
    def get_default_sort(self) -> str:
        """Get default sort setting"""
        return self.settings.get("default_sort", "uploaded_at")
        
    def get_default_order(self) -> str:
        """Get default order setting"""
        return self.settings.get("default_order", "desc")
    
    def save_settings(self, settings: dict):
        settings.pop("secret_key", None)
        self.settings.update(settings)
        self.file_settings.update(settings)
        with open(self.SETTINGS_FILE, 'w') as f:
            json.dump(self.settings, f, indent=2)
    
    @property
    def DB_USER(self) -> str:
        return self.file_settings.get("database", {}).get('user') or os.getenv("POSTGRES_USER") or self.settings.get("database", {}).get('user', 'postgres')

    @property
    def DB_PASSWORD(self) -> str:
        return self.file_settings.get("database", {}).get('password') or os.getenv("POSTGRES_PASSWORD") or self.settings.get("database", {}).get('password', '')

    @property
    def DB_HOST(self) -> str:
        return self.file_settings.get("database", {}).get('host') or os.getenv("POSTGRES_HOST") or self.settings.get("database", {}).get('host', 'localhost')

    @property
    def DB_PORT(self) -> int:
        return int(self.file_settings.get("database", {}).get('port') or os.getenv("POSTGRES_PORT") or self.settings.get("database", {}).get('port', 5432))

    _FORBIDDEN_TEST_DB_NAMES = frozenset({"blombooru", "production", "main", "postgres"})

    @property
    def DB_NAME(self) -> str:
        test_url = os.getenv("TEST_DATABASE_URL", "").strip()
        if self.IS_TEST_ENV:
            if test_url:
                name = self._parse_db_name_from_url(test_url)
                if name.lower() in self._FORBIDDEN_TEST_DB_NAMES:
                    raise RuntimeError(
                        f"VIOLET_ENV=test but TEST_DATABASE_URL points to "
                        f"production-like database {name!r}. "
                        f"Use a test-specific name (e.g. 'blombooru_test'). "
                        f"Refusing to start against production DB."
                    )
                return name
            env_db = os.getenv("POSTGRES_DB", "").strip()
            if not env_db or env_db.lower() in self._FORBIDDEN_TEST_DB_NAMES:
                raise RuntimeError(
                    "VIOLET_ENV=test but no valid test DB configured. "
                    "Set TEST_DATABASE_URL or POSTGRES_DB to a test-specific name "
                    "(e.g. 'blombooru_test'). Refusing to start against production DB."
                )
            return env_db
        return (
            self.file_settings.get("database", {}).get("name")
            or os.getenv("POSTGRES_DB")
            or self.settings.get("database", {}).get("name", "blombooru")
        )

    @property
    def DATABASE_URL(self) -> URL:
        test_url = os.getenv("TEST_DATABASE_URL", "").strip()
        if self.IS_TEST_ENV and test_url:
            return self._parse_test_url(test_url)
        return URL.create(
            drivername="postgresql",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME
        )

    @staticmethod
    def _parse_db_name_from_url(url: str) -> str:
        parts = url.rstrip("/").rsplit("/", 1)
        if len(parts) == 2 and parts[1]:
            name = parts[1].split("?")[0]
            if name:
                return name
        raise RuntimeError(f"Cannot parse DB name from TEST_DATABASE_URL: {url!r}")

    @staticmethod
    def _parse_test_url(url: str) -> URL:
        from sqlalchemy.engine import make_url
        return make_url(url)
    
    @property
    def REDIS_HOST(self) -> str:
        val = self.file_settings.get("redis", {}).get("host")
        if val is not None:
            return val
        return os.getenv("REDIS_HOST", self.settings.get("redis", {}).get("host", "localhost"))
    
    @property
    def REDIS_PORT(self) -> int:
        val = self.file_settings.get("redis", {}).get("port")
        if val is not None:
            return int(val)
        return int(os.getenv("REDIS_PORT", self.settings.get("redis", {}).get("port", 6379)))
    
    @property
    def REDIS_DB(self) -> int:
        val = self.file_settings.get("redis", {}).get("db")
        if val is not None:
            return int(val)
        return int(os.getenv("REDIS_DB", self.settings.get("redis", {}).get("db", 0)))
    
    @property
    def REDIS_PASSWORD(self) -> Optional[str]:
        val = self.file_settings.get("redis", {}).get("password")
        if val is not None:
            return val
        return os.getenv("REDIS_PASSWORD", self.settings.get("redis", {}).get("password"))
    
    @property
    def REDIS_ENABLED(self) -> bool:
        file_enabled = self.file_settings.get("redis", {}).get("enabled")
        if file_enabled is not None:
            if isinstance(file_enabled, bool):
                return file_enabled
            return str(file_enabled).lower() in ("true", "1", "yes")
            
        env_enabled = os.getenv("REDIS_ENABLED")
        if env_enabled is not None:
            return env_enabled.lower() in ("true", "1", "yes")
            
        return self.settings.get("redis", {}).get("enabled", False)
    
    @property
    def SECRET_KEY(self) -> str:
        # env var > settings file > generated default
        return os.getenv("BLOMBOORU_SECRET_KEY") or self.settings["secret_key"]
    
    _OLD_DEFAULT_NAMES = {"Blombooru", "AnimeLocalBooru", "Anime Local Booru"}

    @property
    def APP_NAME(self) -> str:
        val = self.file_settings.get("app_name")
        if val is not None:
            if val in self._OLD_DEFAULT_NAMES:
                return "V.I.O.L.E.T."
            return val
        return os.getenv("APP_NAME", self.settings.get("app_name", "V.I.O.L.E.T."))
    
    @property
    def CURRENT_THEME(self) -> str:
        val = self.file_settings.get("theme")
        if val is not None:
            return val
        return os.getenv("BLOMBOORU_THEME", self.settings.get("theme", "default_dark"))
    
    @property
    def CURRENT_LANGUAGE(self) -> str:
        val = self.file_settings.get("language")
        if val is not None:
            return val
        return os.getenv("BLOMBOORU_LANGUAGE", self.settings.get("language", "en"))
    
    @property
    def IS_FIRST_RUN(self) -> bool:
        return self.settings.get("first_run", True)
        
    @property
    def EXTERNAL_SHARE_URL(self) -> Optional[str]:
        val = self.file_settings.get("external_share_url")
        if val is not None:
            return val
        return os.getenv("BLOMBOORU_EXTERNAL_SHARE_URL") or self.settings.get("external_share_url")
    
    @property
    def REQUIRE_AUTH(self) -> bool:
        val = self.file_settings.get("require_auth")
        if val is not None:
            return bool(val)
        env_val = os.getenv("BLOMBOORU_REQUIRE_AUTH")
        if env_val is not None:
            return env_val.lower() in ("true", "1", "yes")
        return self.settings.get("require_auth", False)
    
    @property
    def SIDEBAR_FILTER_MODE(self) -> str:
        """Get sidebar filter mode: 'rating', 'custom', or 'off'"""
        val = self.file_settings.get("sidebar_filter_mode")
        if val is not None:
            return val
        return os.getenv("BLOMBOORU_SIDEBAR_FILTER_MODE", self.settings.get("sidebar_filter_mode", "rating"))
    
    @property
    def SIDEBAR_CUSTOM_BUTTONS(self) -> List[dict]:
        """Get custom sidebar buttons: list of {title, tags}"""
        val = self.file_settings.get("sidebar_custom_buttons")
        if val is not None:
            return val
        return self.settings.get("sidebar_custom_buttons", [])

    @property
    def MEDIA_TYPE_TAGS(self) -> dict:
        """Get per-media-type automatic upload tags: {image: [...], gif: [...], video: [...]}"""
        val = self.file_settings.get("media_type_tags")
        if val is not None:
            return val
        return self.settings.get("media_type_tags", {"image": [], "gif": [], "video": []})
    
    @property
    def AI_TAGGING_ENABLED(self) -> bool:
        val = os.getenv("AI_TAGGING_ENABLED", "false")
        return val.lower() in ("true", "1", "yes")

    @property
    def AI_GENERAL_THRESHOLD(self) -> float:
        return float(os.getenv("AI_GENERAL_THRESHOLD", "0.35"))

    @property
    def AI_CHARACTER_THRESHOLD(self) -> float:
        return float(os.getenv("AI_CHARACTER_THRESHOLD", "0.65"))

    @property
    def AI_RATING_THRESHOLD(self) -> float:
        return float(os.getenv("AI_RATING_THRESHOLD", "0.50"))

    @property
    def AI_SUGGESTION_THRESHOLD(self) -> float:
        return float(os.getenv("AI_SUGGESTION_THRESHOLD", "0.20"))

    @property
    def AI_TAGGING_BATCH_MAX_ITEMS(self) -> int:
        return int(os.getenv("AI_TAGGING_BATCH_MAX_ITEMS", "10"))

    @property
    def AI_MODEL_NAME(self) -> str:
        return os.getenv("AI_MODEL_NAME", "wd-swinv2-tagger-v3")

    @property
    def AI_AUTO_TAG_AFTER_IMPORT(self) -> bool:
        val = os.getenv("AI_AUTO_TAG_AFTER_IMPORT", "false")
        return val.lower() in ("true", "1", "yes")

    @property
    def AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS(self) -> int:
        return int(os.getenv("AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS", "20"))

    @property
    def AI_AUTO_TAG_AFTER_IMPORT_ONLY_NEW(self) -> bool:
        val = os.getenv("AI_AUTO_TAG_AFTER_IMPORT_ONLY_NEW", "true")
        return val.lower() in ("true", "1", "yes")

    @property
    def AI_AUTO_TAG_AFTER_IMPORT_DRY_RUN(self) -> bool:
        val = os.getenv("AI_AUTO_TAG_AFTER_IMPORT_DRY_RUN", "false")
        return val.lower() in ("true", "1", "yes")

    @property
    def AI_AUTO_TAG_AFTER_IMPORT_FORCE_SUGGESTIONS(self) -> bool:
        val = os.getenv("AI_AUTO_TAG_AFTER_IMPORT_FORCE_SUGGESTIONS", "false")
        return val.lower() in ("true", "1", "yes")

    @property
    def TAG_TRANSLATION_LLM_ENABLED(self) -> bool:
        val = os.getenv("TAG_TRANSLATION_LLM_ENABLED", "false")
        return val.lower() in ("true", "1", "yes")

    @property
    def TAG_TRANSLATION_LLM_PROVIDER(self) -> str:
        return os.getenv("TAG_TRANSLATION_LLM_PROVIDER", "openai_compatible")

    @property
    def TAG_TRANSLATION_LLM_API_KEY(self) -> str:
        return os.getenv("TAG_TRANSLATION_LLM_API_KEY", "")

    @property
    def TAG_TRANSLATION_LLM_MODEL(self) -> str:
        return os.getenv("TAG_TRANSLATION_LLM_MODEL", "")

    @property
    def TAG_TRANSLATION_LLM_BASE_URL(self) -> str:
        return os.getenv("TAG_TRANSLATION_LLM_BASE_URL", "")

    @property
    def TAG_TRANSLATION_BATCH_MAX_ITEMS(self) -> int:
        return int(os.getenv("TAG_TRANSLATION_BATCH_MAX_ITEMS", "50"))

    @property
    def TAG_TRANSLATION_AUTO_ENABLED(self) -> bool:
        val = os.getenv("TAG_TRANSLATION_AUTO_ENABLED", "false")
        return val.lower() in ("true", "1", "yes")

    @property
    def TAG_TRANSLATION_AUTO_MAX_ITEMS(self) -> int:
        return int(os.getenv("TAG_TRANSLATION_AUTO_MAX_ITEMS", "20"))

    @property
    def LOCAL_LIBRARY_PATHS(self) -> List[Path]:
        raw = os.getenv("LOCAL_LIBRARY_PATHS", "")
        if not raw:
            return []
        paths = []
        for p in raw.split("|"):
            p = p.strip()
            if p:
                paths.append(Path(p))
        return paths

    @property
    def SCAN_HYDRATED_ONLY_DEFAULT(self) -> bool:
        val = os.getenv("SCAN_HYDRATED_ONLY_DEFAULT", "true")
        return val.lower() in ("true", "1", "yes")

    @property
    def SCAN_FILE_OPEN_TIMEOUT_SECONDS(self) -> int:
        return int(os.getenv("SCAN_FILE_OPEN_TIMEOUT_SECONDS", "30"))

    @property
    def SCAN_MAX_FILE_SIZE_MB(self) -> int:
        return int(os.getenv("SCAN_MAX_FILE_SIZE_MB", "200"))

    @property
    def SHARED_TAGS_ENABLED(self) -> bool:
        """Check if shared tag database is enabled"""
        file_enabled = self.file_settings.get("shared_tags", {}).get("enabled")
        if file_enabled is not None:
            if isinstance(file_enabled, bool):
                return file_enabled
            return str(file_enabled).lower() in ("true", "1", "yes")
        
        env_enabled = os.getenv("SHARED_TAGS_ENABLED")
        if env_enabled is not None:
            return env_enabled.lower() in ("true", "1", "yes")
        
        return self.settings.get("shared_tags", {}).get("enabled", False)
    
    @property
    def SHARED_TAG_DB_HOST(self) -> str:
        return self.file_settings.get("shared_tags", {}).get("host") or os.getenv("SHARED_TAG_DB_HOST") or self.settings.get("shared_tags", {}).get("host", "localhost")
    
    @property
    def SHARED_TAG_DB_PORT(self) -> int:
        return int(self.file_settings.get("shared_tags", {}).get("port") or os.getenv("SHARED_TAG_DB_PORT") or self.settings.get("shared_tags", {}).get("port", 5432))
    
    @property
    def SHARED_TAG_DB_NAME(self) -> str:
        return self.file_settings.get("shared_tags", {}).get("name") or os.getenv("SHARED_TAG_DB_NAME") or self.settings.get("shared_tags", {}).get("name", "shared_tags")
    
    @property
    def SHARED_TAG_DB_USER(self) -> str:
        return self.file_settings.get("shared_tags", {}).get("user") or os.getenv("SHARED_TAG_DB_USER") or self.settings.get("shared_tags", {}).get("user", "postgres")
    
    @property
    def SHARED_TAG_DB_PASSWORD(self) -> str:
        return self.file_settings.get("shared_tags", {}).get("password") or os.getenv("SHARED_TAG_DB_PASSWORD") or self.settings.get("shared_tags", {}).get("password", "")
    
    @property
    def SHARED_TAG_DATABASE_URL(self) -> URL:
        """Get shared tag database connection URL"""
        return URL.create(
            drivername="postgresql",
            username=self.SHARED_TAG_DB_USER,
            password=self.SHARED_TAG_DB_PASSWORD,
            host=self.SHARED_TAG_DB_HOST,
            port=self.SHARED_TAG_DB_PORT,
            database=self.SHARED_TAG_DB_NAME
        )

    @property
    def TAG_TRANSLATION_BG_ENABLED(self) -> bool:
        val = os.getenv("TAG_TRANSLATION_BACKGROUND_ENABLED", "false")
        return val.lower() in ("true", "1", "yes")

    @property
    def TAG_TRANSLATION_BG_INTERVAL(self) -> int:
        return int(os.getenv("TAG_TRANSLATION_BACKGROUND_INTERVAL_SECONDS", "300"))

    @property
    def TAG_TRANSLATION_BG_BATCH_SIZE(self) -> int:
        return int(os.getenv("TAG_TRANSLATION_BACKGROUND_BATCH_SIZE", "100"))

    @property
    def TAG_TRANSLATION_BG_MAX_PER_RUN(self) -> int:
        return int(os.getenv("TAG_TRANSLATION_BACKGROUND_MAX_PER_RUN", "500"))

    @property
    def TAG_TRANSLATION_BG_DAILY_LIMIT(self) -> int:
        return int(os.getenv("TAG_TRANSLATION_BACKGROUND_DAILY_LIMIT", "5000"))

    @property
    def TAG_TRANSLATION_BG_ERROR_LIMIT(self) -> int:
        return int(os.getenv("TAG_TRANSLATION_BACKGROUND_ERROR_LIMIT", "5"))

    @property
    def TAG_TRANSLATION_BG_PRIORITY(self) -> str:
        return os.getenv("TAG_TRANSLATION_BACKGROUND_PRIORITY", "post_count")

    @property
    def TAG_TRANSLATION_BG_CATEGORIES(self) -> list:
        raw = os.getenv("TAG_TRANSLATION_BACKGROUND_CATEGORIES", "general,meta")
        return [c.strip() for c in raw.split(",") if c.strip()]

    # LLM Fallback Provider
    @property
    def TAG_TRANSLATION_LLM_FALLBACK_API_KEY(self) -> str:
        return os.getenv("TAG_TRANSLATION_LLM_FALLBACK_API_KEY", "")

    @property
    def TAG_TRANSLATION_LLM_FALLBACK_MODEL(self) -> str:
        return os.getenv("TAG_TRANSLATION_LLM_FALLBACK_MODEL", "")

    @property
    def TAG_TRANSLATION_LLM_FALLBACK_BASE_URL(self) -> str:
        return os.getenv("TAG_TRANSLATION_LLM_FALLBACK_BASE_URL", "")

    # Entity Alias Resolver (Phase 2.3e)
    @property
    def ENTITY_ALIAS_RESOLVER_ENABLED(self) -> bool:
        val = os.getenv("ENTITY_ALIAS_RESOLVER_ENABLED", "false")
        return val.lower() in ("true", "1", "yes")

    @property
    def ENTITY_ALIAS_BATCH_SIZE(self) -> int:
        return int(os.getenv("ENTITY_ALIAS_BATCH_SIZE", "20"))

    @property
    def ENTITY_ALIAS_MAX_PER_RUN(self) -> int:
        return int(os.getenv("ENTITY_ALIAS_MAX_PER_RUN", "100"))

    @property
    def CONTENT_CLASSIFICATION_ENABLED(self) -> bool:
        val = os.getenv("CONTENT_CLASSIFICATION_ENABLED", "false")
        return val.lower() in ("true", "1", "yes")

    @property
    def CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS(self) -> int:
        return int(os.getenv("CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS", "100"))

    @property
    def CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT(self) -> bool:
        val = os.getenv("CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT", "false")
        return val.lower() in ("true", "1", "yes")

    @property
    def CONTENT_CLASSIFICATION_AUTO_MAX_ITEMS(self) -> int:
        return int(os.getenv("CONTENT_CLASSIFICATION_AUTO_MAX_ITEMS", "50"))

    @property
    def CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD(self) -> int:
        return int(os.getenv("CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD", "5"))

    @property
    def CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD(self) -> float:
        return float(os.getenv("CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD", "0.5"))

    @property
    def CONTENT_CLASSIFICATION_METHOD(self) -> str:
        return os.getenv("CONTENT_CLASSIFICATION_METHOD", "clip")

    @property
    def CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN(self) -> float:
        return float(os.getenv("CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN", "0.005"))

settings = Settings()
