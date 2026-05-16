# ~/projects/agent-shield/setup.py
from setuptools import setup, find_packages

setup(
    name="agent-shield",
    version="0.1.0",
    author="zeus",
    description="Security gateway proxy & library to defend autonomous AI agents from Indirect Prompt Injections",
    long_description=open("README.md").read() if open("README.md") else "",
    long_description_content_type="text/markdown",
    packages=find_packages(exclude=["tests*", "api*"]),
    install_requires=[
        "httpx>=0.24.0",
        "pydantic>=2.0.0",
        "fastapi>=0.100.0",
        "uvicorn>=0.22.0"
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.10",
)

