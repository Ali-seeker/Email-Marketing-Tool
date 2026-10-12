import os

# Disable SQLAlchemy Cython extensions to avoid Python 3.14 coverage conflict
os.environ["DISABLE_SQLALCHEMY_CEXT"] = "1"
