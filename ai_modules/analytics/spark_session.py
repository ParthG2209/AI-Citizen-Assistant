"""Local-mode SparkSession tuned for an 8 GB laptop.

Environment variables (all optional):

* ``JAVA_HOME`` - JDK used by Spark (17 recommended); otherwise ``java`` must be on ``PATH``.
* ``SPARK_MASTER`` (default ``local[2]``), ``SPARK_DRIVER_MEMORY`` (default ``1g``),
  ``SPARK_SHUFFLE_PARTITIONS`` (default ``4``).

The Spark UI is disabled and Arrow is used for ``toPandas()``. pyspark is imported lazily.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

APP_NAME = "aca-district-analytics"


class JavaNotFoundError(RuntimeError):
    """No JDK available for PySpark."""


@dataclass(frozen=True, slots=True)
class SparkSettings:
    master: str = "local[2]"
    driver_memory: str = "1g"
    shuffle_partitions: int = 4

    @classmethod
    def from_env(cls) -> SparkSettings:
        default = cls()
        return cls(
            master=os.environ.get("SPARK_MASTER", default.master),
            driver_memory=os.environ.get("SPARK_DRIVER_MEMORY", default.driver_memory),
            shuffle_partitions=int(os.environ.get("SPARK_SHUFFLE_PARTITIONS", default.shuffle_partitions)),
        )


def find_java() -> Path:
    """Locate the ``java`` executable via ``JAVA_HOME`` first, then ``PATH``."""
    executable = "java.exe" if os.name == "nt" else "java"
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / executable
        if candidate.is_file():
            return candidate
        raise JavaNotFoundError(f"JAVA_HOME is set to '{java_home}' but {candidate} does not exist.")
    on_path = shutil.which("java")
    if on_path:
        return Path(on_path)
    raise JavaNotFoundError(
        "PySpark needs a Java runtime (JDK 17 recommended) but none was found. Either set JAVA_HOME to a JDK "
        "directory, or install a portable JDK without admin rights, e.g.\n"
        "  uvx --from install-jdk python -c \"import jdk; print(jdk.install('17', path='~/.jdks'))\"\n"
        "then set JAVA_HOME to the printed path. Alternatively run the analytics with --engine pandas."
    )


# Characters that break Spark's Windows launcher batch files (bin/*.cmd) when they appear in a path.
_CMD_UNSAFE = frozenset("()&^%!")


def _cmd_safe(path: str) -> bool:
    return not any(char in _CMD_UNSAFE for char in path)


def _prepare_windows_launcher() -> None:
    """Work around Spark's ``.cmd`` launchers on Windows.

    ``pyspark`` starts the JVM through ``bin\\spark-submit.cmd``, whose ``if (...)`` blocks break when
    ``SPARK_HOME`` or ``PYSPARK_PYTHON`` contain ``(`` / ``)`` (e.g. a repo in ``D:\\New folder (5)``).
    If needed, ``SPARK_HOME`` is pointed at a directory junction (no admin rights needed) under
    ``%LOCALAPPDATA%\\aca-citizen-assistant`` that targets the installed pyspark package.
    """
    import pyspark

    if os.environ.get("PYSPARK_PYTHON") and not _cmd_safe(os.environ["PYSPARK_PYTHON"]):
        raise RuntimeError(
            f"PYSPARK_PYTHON='{os.environ['PYSPARK_PYTHON']}' contains characters that break Spark's Windows "
            "launcher; unset it (the analytics job does not need Python workers)."
        )
    if os.environ.get("SPARK_HOME"):
        return
    package_dir = Path(pyspark.__file__).resolve().parent
    if _cmd_safe(str(package_dir)):
        os.environ["SPARK_HOME"] = str(package_dir)
        return
    import _winapi

    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "aca-citizen-assistant"
    junction = base / f"spark-home-{pyspark.__version__}"
    if not _cmd_safe(str(junction)):
        raise RuntimeError(
            f"Cannot find a Spark-safe path for SPARK_HOME; set SPARK_HOME to a copy of {package_dir}"
        )
    if not junction.exists():
        base.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(package_dir), str(junction))
    os.environ["SPARK_HOME"] = str(junction)


def get_spark(settings: SparkSettings | None = None) -> SparkSession:
    """Create (or reuse) the local SparkSession. Raises ``JavaNotFoundError`` if no JDK is available."""
    settings = settings or SparkSettings.from_env()
    java = find_java()
    os.environ.setdefault("JAVA_HOME", str(java.parent.parent))
    if os.name == "nt":
        _prepare_windows_launcher()
    elif not os.environ.get("PYSPARK_PYTHON"):
        # Python workers (if ever needed) must use this interpreter, not whatever "python" is on PATH.
        os.environ["PYSPARK_PYTHON"] = sys.executable

    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.master(settings.master)
        .appName(APP_NAME)
        .config("spark.driver.memory", settings.driver_memory)
        .config("spark.sql.shuffle.partitions", str(settings.shuffle_partitions))
        .config("spark.default.parallelism", str(settings.shuffle_partitions))
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.execution.arrow.pyspark.enabled", "true")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    # Read by PySpark when it starts Python workers (after the JVM launch, so a path with "(" is fine here).
    spark.sparkContext.pythonExec = os.environ.get("PYSPARK_PYTHON", sys.executable)
    return spark
