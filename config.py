from pydantic import BaseModel


class Config(BaseModel):
    """Plugin Config Here"""
    # 配置Twikit客户端
    X_USERNAME: str = ''
    X_EMAIL: str = ''
    X_PASSWORD: str = ''