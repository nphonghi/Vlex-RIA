import pathlib

from setuptools import setup, find_packages

CWD = pathlib.Path(__file__).absolute().parent


def get_version():
    """Gets the Vlex-RIA version."""
    path = CWD / "vlex_ria" / "__init__.py"
    if path.exists():
        content = path.read_text()
        for line in content.splitlines():
            if line.startswith("__version__"):
                return line.strip().split()[-1].strip().strip('"')
    return "1.0.0"


def get_requirements():
    """Gets requirements from requirements.txt."""
    path = CWD / "requirements.txt"
    if path.exists():
        return [
            line.strip()
            for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    return []


setup(
    name="vlex-ria",
    version=get_version(),
    description="Hệ thống tự động đánh giá tác động chính sách",
    packages=find_packages(),
    install_requires=get_requirements(),
)
