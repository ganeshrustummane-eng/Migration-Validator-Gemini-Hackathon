"""
Run ID and timestamp generation utilities
"""
from datetime import datetime


def generate_runid():
    """
    Generate unique run IDs for batch validation tracking
    
    Returns:
        tuple: (short_id, long_id)
            short_id: "20260814_125125_450664" (for file naming)
            long_id: "14-Aug-2026 12:51:25.450664" (for display)
    
    Example:
        >>> short_id, long_id = generate_runid()
        >>> print(f"Run started at: {long_id}")
    """
    return (
        datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
        datetime.now().strftime("%d-%b-%Y %H:%M:%S.%f")
    )


if __name__ == "__main__":
    short_id, long_id = generate_runid()
    print(f"Short ID: {short_id}")
    print(f"Long ID:  {long_id}")
