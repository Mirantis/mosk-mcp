"""Tests for cluster configuration models."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mosk_mcp.cluster.config import (
    ClusterConfig,
    ClusterEnvironment,
    ClustersConfig,
)


class TestClusterEnvironment:
    """Tests for ClusterEnvironment enum."""

    def test_development_environment(self) -> None:
        """Test development environment value."""
        assert ClusterEnvironment.DEVELOPMENT.value == "development"

    def test_staging_environment(self) -> None:
        """Test staging environment value."""
        assert ClusterEnvironment.STAGING.value == "staging"

    def test_production_environment(self) -> None:
        """Test production environment value."""
        assert ClusterEnvironment.PRODUCTION.value == "production"


class TestClusterConfig:
    """Tests for ClusterConfig model."""

    def test_minimal_config(self) -> None:
        """Test creating cluster config with minimal required fields."""
        config = ClusterConfig(url="https://mcc.example.com")
        assert config.url == "https://mcc.example.com"
        assert config.environment == ClusterEnvironment.DEVELOPMENT
        assert config.ssl_verify is True
        assert config.is_locked is False

    def test_full_config(self) -> None:
        """Test creating cluster config with all fields."""
        config = ClusterConfig(
            url="https://mcc-prod.example.com",
            name="Production MCC",
            environment=ClusterEnvironment.PRODUCTION,
            ssl_verify=True,
            fingerprint="abc123",
            is_locked=True,
            description="Production cluster",
        )
        assert config.url == "https://mcc-prod.example.com"
        assert config.name == "Production MCC"
        assert config.environment == ClusterEnvironment.PRODUCTION
        assert config.ssl_verify is True
        assert config.fingerprint == "abc123"
        assert config.is_locked is True
        assert config.description == "Production cluster"

    def test_production_requires_https(self) -> None:
        """Test that production clusters require HTTPS."""
        with pytest.raises(ValidationError) as exc_info:
            ClusterConfig(
                url="http://mcc-prod.example.com",
                environment=ClusterEnvironment.PRODUCTION,
            )
        assert "Production clusters MUST use HTTPS" in str(exc_info.value)

    def test_production_requires_ssl_verify(self) -> None:
        """Test that production clusters require SSL verification."""
        with pytest.raises(ValidationError) as exc_info:
            ClusterConfig(
                url="https://mcc-prod.example.com",
                environment=ClusterEnvironment.PRODUCTION,
                ssl_verify=False,
            )
        assert "Production clusters MUST have SSL verification enabled" in str(exc_info.value)

    def test_development_allows_http(self) -> None:
        """Test that development clusters allow HTTP."""
        config = ClusterConfig(
            url="http://172.16.166.22",
            environment=ClusterEnvironment.DEVELOPMENT,
        )
        assert config.url == "http://172.16.166.22"

    def test_development_allows_no_ssl_verify(self) -> None:
        """Test that development clusters allow disabled SSL verification."""
        config = ClusterConfig(
            url="https://172.16.166.22",
            environment=ClusterEnvironment.DEVELOPMENT,
            ssl_verify=False,
        )
        assert config.ssl_verify is False

    def test_compute_fingerprint(self) -> None:
        """Test fingerprint computation."""
        config = ClusterConfig(url="https://mcc.example.com")
        keycloak_issuer = "https://mcc.example.com/auth/realms/iam"
        k8s_api_url = "https://mcc.example.com:6443"

        fingerprint = config.compute_fingerprint(keycloak_issuer, k8s_api_url)

        # Verify it's a valid hex string of correct length
        assert len(fingerprint) == 32
        assert all(c in "0123456789abcdef" for c in fingerprint)

        # Verify deterministic computation
        fingerprint2 = config.compute_fingerprint(keycloak_issuer, k8s_api_url)
        assert fingerprint == fingerprint2

    def test_fingerprint_changes_with_different_inputs(self) -> None:
        """Test that fingerprint changes with different inputs."""
        config = ClusterConfig(url="https://mcc.example.com")

        fp1 = config.compute_fingerprint("https://issuer1", "https://api1")
        fp2 = config.compute_fingerprint("https://issuer2", "https://api1")
        fp3 = config.compute_fingerprint("https://issuer1", "https://api2")

        assert fp1 != fp2
        assert fp1 != fp3
        assert fp2 != fp3

    def test_invalid_url(self) -> None:
        """Test that invalid URLs are rejected."""
        with pytest.raises(ValidationError):
            ClusterConfig(url="not-a-valid-url")

    def test_empty_url(self) -> None:
        """Test that empty URL is rejected."""
        with pytest.raises(ValidationError):
            ClusterConfig(url="")


class TestClustersConfig:
    """Tests for ClustersConfig model."""

    def test_empty_config(self) -> None:
        """Test creating empty clusters config."""
        config = ClustersConfig(clusters={})
        assert config.clusters == {}
        assert config.active is None
        assert config.confirm_production_switch is True

    def test_config_with_clusters(self) -> None:
        """Test creating config with multiple clusters."""
        config = ClustersConfig(
            active="dev",
            clusters={
                "dev": ClusterConfig(
                    url="https://172.16.166.22",
                    name="Development",
                    environment=ClusterEnvironment.DEVELOPMENT,
                    ssl_verify=False,
                ),
                "prod": ClusterConfig(
                    url="https://mcc-prod.example.com",
                    name="Production",
                    environment=ClusterEnvironment.PRODUCTION,
                ),
            },
        )
        assert len(config.clusters) == 2
        assert config.active == "dev"
        assert config.clusters["dev"].name == "Development"
        assert config.clusters["prod"].name == "Production"

    def test_active_cluster_must_exist(self) -> None:
        """Test that active cluster must exist in clusters dict."""
        with pytest.raises(ValidationError) as exc_info:
            ClustersConfig(
                active="nonexistent",
                clusters={
                    "dev": ClusterConfig(url="https://mcc.example.com"),
                },
            )
        assert "Active cluster 'nonexistent' not found" in str(exc_info.value)

    def test_load_from_yaml(self, tmp_path: Path) -> None:
        """Test loading config from YAML file."""
        yaml_content = """
active: dev
confirm_production_switch: true
clusters:
  dev:
    url: "https://172.16.166.22"
    name: "Development"
    environment: development
    ssl_verify: false
  prod:
    url: "https://mcc-prod.example.com"
    name: "Production"
    environment: production
"""
        config_file = tmp_path / "clusters.yaml"
        config_file.write_text(yaml_content)

        config = ClustersConfig.from_yaml_file(config_file)

        assert config.active == "dev"
        assert len(config.clusters) == 2
        assert config.clusters["dev"].ssl_verify is False
        assert config.clusters["prod"].environment == ClusterEnvironment.PRODUCTION

    def test_save_to_yaml(self, tmp_path: Path) -> None:
        """Test saving config to YAML file."""
        config = ClustersConfig(
            active="dev",
            clusters={
                "dev": ClusterConfig(
                    url="https://mcc.example.com",
                    name="Development",
                ),
            },
        )

        config_file = tmp_path / "clusters.yaml"
        config.to_yaml_file(config_file)

        # Reload and verify
        loaded = ClustersConfig.from_yaml_file(config_file)
        assert loaded.active == "dev"
        assert loaded.clusters["dev"].url == "https://mcc.example.com"

    def test_load_nonexistent_file(self, tmp_path: Path) -> None:
        """Test loading from nonexistent file returns default config."""
        config_file = tmp_path / "nonexistent.yaml"

        config = ClustersConfig.from_yaml_file(config_file)

        assert config.active is None
        assert config.clusters == {}

    def test_get_cluster_by_dict_access(self) -> None:
        """Test getting a cluster by ID via dict access."""
        config = ClustersConfig(
            clusters={
                "dev": ClusterConfig(url="https://mcc.example.com"),
            },
        )

        cluster = config.clusters.get("dev")
        assert cluster is not None
        assert cluster.url == "https://mcc.example.com"

        # Nonexistent cluster
        assert config.clusters.get("nonexistent") is None

    def test_get_active_cluster(self) -> None:
        """Test getting the active cluster."""
        config = ClustersConfig(
            active="dev",
            clusters={
                "dev": ClusterConfig(url="https://mcc.example.com"),
                "prod": ClusterConfig(
                    url="https://mcc-prod.example.com",
                    environment=ClusterEnvironment.PRODUCTION,
                ),
            },
        )

        cluster_id, cluster = config.get_active_cluster()
        assert cluster_id == "dev"
        assert cluster is not None
        assert cluster.url == "https://mcc.example.com"

    def test_get_active_cluster_none_set(self) -> None:
        """Test getting active cluster when none is set."""
        config = ClustersConfig(
            clusters={
                "dev": ClusterConfig(url="https://mcc.example.com"),
            },
        )

        cluster_id, cluster = config.get_active_cluster()
        assert cluster_id is None
        assert cluster is None
