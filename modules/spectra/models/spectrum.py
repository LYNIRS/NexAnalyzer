"""
Data models for spectrum files and processing state.

This module defines the core data structures for spectra analysis:
- SpectrumData: Immutable X/Y array pair with validation
- ProcessingSettings: De-spike and baseline parameters
- SpectrumFile: Container for a single .txt file's data and state
"""

from dataclasses import dataclass, field
from typing import Optional, Literal
import numpy as np

#: Allowed despike threshold (modified Z-score) -- the despiker, the processing
#: settings, the preset block and the preset editor all check against this.
#: The Z-score is taken against the whole spectrum's median and MAD, so a strong
#: real peak can score like a cosmic ray: on WSe2 TSM260500, E2g scores 12.6-30.4
#: and the likely cosmic rays 89-1521. Raised from 30 at v5.10.5 so a preset can
#: sit above its own peaks; the WSe2 presets use 100.
DESPIKE_THRESHOLD_MIN = 3.0
DESPIKE_THRESHOLD_MAX = 500.0


@dataclass(frozen=True)
class SpectrumData:
    """
    Immutable X/Y array pair representing spectroscopy data.

    Attributes
    ----------
    X : np.ndarray
        Wavenumber (cm⁻¹) for Raman or wavelength (nm) for PL.
        Must be 1D float64 array with 100-100000 points.
    Y : np.ndarray
        Intensity in raw detector units (may be negative for background-subtracted spectra).
        Must be same length as X, 1D float64 array.

    Raises
    ------
    ValueError
        If validation fails (length mismatch, wrong dtype, NaN/Inf values, etc.)

    Notes
    -----
    v2.1+: Y values may be negative to support background-subtracted spectra and detector offsets.
    Baseline correction algorithms automatically handle negative Y via internal shifting.
    """

    X: np.ndarray
    Y: np.ndarray

    def __post_init__(self):
        """Validate arrays after initialization."""
        # Convert to numpy arrays if needed
        if not isinstance(self.X, np.ndarray):
            object.__setattr__(self, 'X', np.array(self.X, dtype=np.float64))
        if not isinstance(self.Y, np.ndarray):
            object.__setattr__(self, 'Y', np.array(self.Y, dtype=np.float64))

        # Validation
        if self.X.ndim != 1 or self.Y.ndim != 1:
            raise ValueError("X and Y must be 1D arrays")

        if len(self.X) != len(self.Y):
            raise ValueError(f"X and Y must have same length (got {len(self.X)} vs {len(self.Y)})")

        if len(self.X) < 100:
            raise ValueError(f"Spectrum too short: {len(self.X)} points (minimum 100)")

        if len(self.X) > 100000:
            raise ValueError(f"Spectrum too long: {len(self.X)} points (maximum 100000)")

        if self.X.dtype != np.float64:
            object.__setattr__(self, 'X', self.X.astype(np.float64))

        if self.Y.dtype != np.float64:
            object.__setattr__(self, 'Y', self.Y.astype(np.float64))

        # v2.1: Removed negative Y check to support background-subtracted spectra
        # Baseline algorithms handle negative values via automatic internal shifting

        if not np.all(np.isfinite(self.X)) or not np.all(np.isfinite(self.Y)):
            raise ValueError("X and Y must not contain NaN or Inf values")

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON export."""
        return {
            "X": self.X.tolist(),
            "Y": self.Y.tolist()
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SpectrumData":
        """Deserialize from dictionary."""
        return cls(
            X=np.array(data["X"], dtype=np.float64),
            Y=np.array(data["Y"], dtype=np.float64)
        )


@dataclass
class ProcessingSettings:
    """
    De-spiking and baseline correction parameters.

    Attributes
    ----------
    despike_threshold : float
        Modified Z-score threshold for spike detection
        (DESPIKE_THRESHOLD_MIN to DESPIKE_THRESHOLD_MAX).
    despike_applied : bool
        Whether spike removal has been run.
    baseline_algorithm : Literal["Polynomial", "ALS"]
        Baseline correction method.
    baseline_degree : int
        Polynomial degree (1-10, used if baseline_algorithm='Polynomial').
    baseline_lambda : float
        ALS smoothness parameter (1e3-1e6, used if baseline_algorithm='ALS').
    baseline_p : float
        ALS asymmetry parameter (0.001-0.1, used if baseline_algorithm='ALS').
    baseline_applied : bool
        Whether baseline correction has been run.
    y_shift : float
        Automatic Y-shift applied for baseline stability (v2.1+). 0 if not applied.
    """

    despike_threshold: float = 30.0
    despike_applied: bool = False
    baseline_algorithm: Literal["Polynomial", "ALS"] = "ALS"
    baseline_degree: int = 3
    baseline_lambda: float = 10000.0
    baseline_p: float = 0.001
    baseline_applied: bool = False
    y_shift: float = 0.0

    def __post_init__(self):
        """Validate parameters."""
        if not (DESPIKE_THRESHOLD_MIN <= self.despike_threshold <= DESPIKE_THRESHOLD_MAX):
            raise ValueError(
                f"despike_threshold must be in [{DESPIKE_THRESHOLD_MIN}, {DESPIKE_THRESHOLD_MAX}] "
                f"(got {self.despike_threshold})"
            )

        if self.baseline_algorithm not in ["Polynomial", "ALS"]:
            raise ValueError(f"baseline_algorithm must be 'Polynomial' or 'ALS' (got {self.baseline_algorithm})")

        if not (1 <= self.baseline_degree <= 10):
            raise ValueError(f"baseline_degree must be in [1, 10] (got {self.baseline_degree})")

        if not (1000 <= self.baseline_lambda <= 1000000):
            raise ValueError(f"baseline_lambda must be in [1e3, 1e6] (got {self.baseline_lambda})")

        if not (0.001 <= self.baseline_p <= 0.1):
            raise ValueError(f"baseline_p must be in [0.001, 0.1] (got {self.baseline_p})")

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON export."""
        return {
            "despike_threshold": self.despike_threshold,
            "despike_applied": self.despike_applied,
            "baseline_algorithm": self.baseline_algorithm,
            "baseline_degree": self.baseline_degree,
            "baseline_lambda": self.baseline_lambda,
            "baseline_p": self.baseline_p,
            "baseline_applied": self.baseline_applied,
            "y_shift": self.y_shift
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProcessingSettings":
        """Deserialize from dictionary (with v2.0 backward compatibility)."""
        # v2.1: Add default for y_shift if missing (v2.0 compatibility)
        if "y_shift" not in data:
            data = data.copy()
            data["y_shift"] = 0.0
        return cls(**data)


@dataclass
class SpectrumFile:
    """
    Container for a single .txt file's data and processing state.

    Attributes
    ----------
    filename : str
        Original .txt filename (1-255 characters).
    source_dir : Optional[str]
        Absolute path to the folder the .txt was loaded from (v2.7+).
        None for files from old projects that predate this field. Used to
        default the "Save Master CSV to folder" dialog to the raw-data folder.
    mode : Literal["Raman", "PL"]
        Spectroscopy mode (affects units and fitting bounds).
    original_data : SpectrumData
        Unmodified original X/Y arrays from file load (v2.2+, Issue 5 fix).
        Never modified after initial load, used for full reset.
    raw_data : SpectrumData
        Original X/Y arrays (may be cropped by X-range, never modified by processing).
    processed_data : SpectrumData
        Current processed X/Y arrays (after despike/baseline).
    processing_settings : ProcessingSettings
        De-spike and baseline configuration.
    peak_table : list
        User-defined peak definitions (list of PeakDefinition objects).
    fit_result : Optional[FitResult]
        Fitted parameters and quality metrics (None if not yet fitted).
    auto_detected : bool
        Whether mode was auto-detected from filename (v2.1+, FR-12).
    x_range_enabled : bool
        Whether X-range limiting is active (v2.1+, FR-15).
    x_min : Optional[float]
        Minimum X value for processing range (v2.1+, FR-15).
    x_max : Optional[float]
        Maximum X value for processing range (v2.1+, FR-15).
    despike_done : bool
        Whether de-spiking has been successfully completed (v2.2+).
    baseline_done : bool
        Whether baseline correction has been successfully completed (v2.2+).
    fit_done : bool
        Whether peak fitting has been successfully completed (v2.2+).
    fit_stale : bool
        Whether preprocessing changed after fit (requires refitting) (v2.2+).
    last_preprocessing_hash : Optional[str]
        SHA256 hash of (despike + baseline params) for staleness detection (v2.2+).
    background : Optional[np.ndarray]
        The baseline subtracted from `processed_data`, on its X, in original
        scale (v5.13.0). Kept so the fit can be drawn sitting on the background
        it was separated from: the de-spiked data is `processed_data.Y +
        background`. None until a baseline is applied, and cleared by anything
        that replaces `processed_data` with a series it doesn't describe.
    """

    filename: str
    mode: Literal["Raman", "PL"]
    original_data: SpectrumData  # **NEW (Issue 5)**: True original before X-range cropping
    raw_data: SpectrumData
    processed_data: SpectrumData
    source_dir: Optional[str] = None  # **NEW (v2.7)**: folder the .txt was loaded from
    processing_settings: ProcessingSettings = field(default_factory=ProcessingSettings)
    peak_table: list = field(default_factory=list)
    fit_result: Optional[object] = None  # Will be FitResult after peak.py is implemented
    auto_detected: bool = False
    x_range_enabled: bool = False
    x_min: Optional[float] = None
    x_max: Optional[float] = None
    # v2.2: Status tracking for workflow orchestration
    despike_done: bool = False
    baseline_done: bool = False
    fit_done: bool = False
    fit_stale: bool = False
    last_preprocessing_hash: Optional[str] = None
    background: Optional[np.ndarray] = None  # v5.13.0: the subtracted baseline

    def __post_init__(self):
        """Validate attributes."""
        if not (1 <= len(self.filename) <= 255):
            raise ValueError(f"filename must be 1-255 characters (got {len(self.filename)})")

        if self.mode not in ["Raman", "PL"]:
            raise ValueError(f"mode must be 'Raman' or 'PL' (got {self.mode})")

    def reset_to_raw(self):
        """
        Reset to original unmodified data (before X-range or processing).

        This reverts:
        - X-range cropping (restores full original data)
        - De-spiking
        - Baseline correction
        - Peak fitting

        Notes
        -----
        v2.2+: Resets to original_data (full uncropped data), not raw_data.
        Also clears X-range settings and all status flags.
        """
        # **FIX (Issue 5)**: Reset to true original data (before any X-range cropping)
        self.original_data = SpectrumData(
            X=self.original_data.X.copy(),
            Y=self.original_data.Y.copy()
        )
        self.raw_data = SpectrumData(
            X=self.original_data.X.copy(),
            Y=self.original_data.Y.copy()
        )
        self.processed_data = SpectrumData(
            X=self.original_data.X.copy(),
            Y=self.original_data.Y.copy()
        )

        # Reset X-range settings
        self.x_range_enabled = False
        self.x_min = None
        self.x_max = None

        self.processing_settings = ProcessingSettings()
        self.fit_result = None

        # Clear status flags
        self.despike_done = False
        self.baseline_done = False
        self.fit_done = False
        self.fit_stale = False
        self.last_preprocessing_hash = None
        self.background = None

    def to_dict(self, include_arrays: bool = True) -> dict:
        """
        Serialize to dictionary for JSON export.

        Parameters
        ----------
        include_arrays : bool
            If False, exclude raw_data and processed_data arrays to reduce file size.
        """
        result = {
            "filename": self.filename,
            "source_dir": self.source_dir,
            "mode": self.mode,
            "processing_settings": self.processing_settings.to_dict(),
            "peak_table": [p.to_dict() for p in self.peak_table] if self.peak_table else [],
            "fit_result": self.fit_result.to_dict() if self.fit_result else None,
            "auto_detected": self.auto_detected,
            "x_range_enabled": self.x_range_enabled,
            "x_min": self.x_min,
            "x_max": self.x_max,
            # v2.2: Status tracking fields
            "despike_done": self.despike_done,
            "baseline_done": self.baseline_done,
            "fit_done": self.fit_done,
            "fit_stale": self.fit_stale,
            "last_preprocessing_hash": self.last_preprocessing_hash
        }

        if include_arrays:
            result["original_data"] = self.original_data.to_dict()  # **NEW (Issue 5)**
            result["raw_data"] = self.raw_data.to_dict()
            result["processed_data"] = self.processed_data.to_dict()
            result["background"] = self.background.tolist() if self.background is not None else None
        else:
            result["original_data"] = None
            result["raw_data"] = None
            result["processed_data"] = None
            result["background"] = None

        return result

    @classmethod
    def from_dict(cls, data: dict) -> "SpectrumFile":
        """Deserialize from dictionary (with v2.0 backward compatibility)."""
        # Import here to avoid circular dependency
        from .peak import PeakDefinition, FitResult

        # **FIX (Issue 5)**: Backward compatibility for original_data
        # If original_data is missing (old v2.2 files), use raw_data as fallback
        original_data = (SpectrumData.from_dict(data["original_data"])
                         if data.get("original_data")
                         else SpectrumData.from_dict(data["raw_data"]) if data.get("raw_data") else None)

        raw_data = SpectrumData.from_dict(data["raw_data"]) if data.get("raw_data") else None
        processed_data = SpectrumData.from_dict(data["processed_data"]) if data.get("processed_data") else None

        if original_data is None or raw_data is None or processed_data is None:
            raise ValueError("Cannot load SpectrumFile without original_data, raw_data, and processed_data")

        # v2.1/v2.2: Add defaults for new fields if missing (backward compatibility)
        return cls(
            filename=data["filename"],
            mode=data["mode"],
            original_data=original_data,  # **NEW (Issue 5)**
            raw_data=raw_data,
            processed_data=processed_data,
            source_dir=data.get("source_dir", None),  # **NEW (v2.7)**: backward-compatible
            processing_settings=ProcessingSettings.from_dict(data["processing_settings"]),
            peak_table=[PeakDefinition.from_dict(p) for p in data.get("peak_table", [])],
            fit_result=FitResult.from_dict(data["fit_result"]) if data.get("fit_result") else None,
            auto_detected=data.get("auto_detected", False),
            x_range_enabled=data.get("x_range_enabled", False),
            x_min=data.get("x_min", None),
            x_max=data.get("x_max", None),
            # v2.2: Status tracking fields (defaults for v2.0/v2.1 projects)
            despike_done=data.get("despike_done", False),
            baseline_done=data.get("baseline_done", False),
            fit_done=data.get("fit_done", False),
            fit_stale=data.get("fit_stale", False),
            last_preprocessing_hash=data.get("last_preprocessing_hash", None),
            # v5.13.0: projects saved earlier have no background; they plot
            # baseline-corrected until re-run.
            background=(np.array(data["background"], dtype=np.float64)
                        if data.get("background") is not None else None),
        )
