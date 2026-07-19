from dataclasses import dataclass


@dataclass
class S3Config:
    access_key: str = ""
    secret_key: str = ""
    bucket: str = ""
    provider: str = ""
    region: str = ""
    endpoint: str = ""
    is_minio: bool = False

    @property
    def is_configured(self) -> bool:
        if self.is_minio:
            return all([self.access_key, self.secret_key, self.bucket, self.endpoint])
        return all([self.access_key, self.secret_key, self.bucket, self.provider, self.region])
