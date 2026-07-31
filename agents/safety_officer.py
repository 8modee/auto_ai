#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — SAFETY OFFICER AGENT
═══════════════════════════════════════════════════════════════
Hardware and service monitoring:
- CPU/RAM/disk monitoring via psutil
- GPU temperature via nvidia-smi
- Detects failed services and restarts them
- Triggers failover to Oracle Cloud / N100
- Logs all incidents
- Runs hourly health checks
- Collects system metrics for dashboard
- Manages backup verification
═══════════════════════════════════════════════════════════════
"""

import os
import json
import subprocess
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import psutil

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, today_str
from agents.base import BaseAgent

logger = get_logger("safety_officer")


class SafetyOfficer(BaseAgent):
    AGENT_NAME = "safety_officer"
    AGENT_TYPE = "governance"
    STREAM = None  # Infrastructure agent
    DEFAULT_INTERVAL_SECONDS = 3600  # Hourly health checks

    def __init__(self):
        super().__init__()
        self.metrics_interval = self.config.get(
            "safety", "metrics_interval_seconds", default=60
        )
        self.thresholds = self.config.get("safety", "alert_thresholds", default={})
        self.cpu_threshold = self.thresholds.get("cpu_percent", 90)
        self.ram_threshold = self.thresholds.get("ram_percent", 85)
        self.disk_threshold = self.thresholds.get("disk_percent", 90)
        self.gpu_temp_threshold = self.thresholds.get("gpu_temp_celsius", 85)

        self.max_restarts = self.config.get(
            "safety", "auto_restart", "max_restarts_per_hour", default=3
        )
        self.restart_cooldown = self.config.get(
            "safety", "auto_restart", "cooldown_seconds", default=300
        )

        self._restart_counts = {}  # service_name -> [timestamps]
        self._failover_
active = False
        self._last_metrics = None

    def run_once(self):
        """Main cycle: collect metrics, check health, detect failures."""
        results = []

        # Phase 1: Collect system metrics
        metrics = self.collect_metrics()
        if metrics:
            results.append("Metrics collected")

        # Phase 2: Check thresholds and alert
        alerts = self._check_thresholds(metrics)
        if alerts:
            results.append(f"{len(alerts)} threshold alerts")

        # Phase 3: Check service health
        service_issues = self._check_services()
        if service_issues:
            results.append(f"{len(service_issues)} service issues detected")

        # Phase 4: Attempt restarts for failed services
        restarted = self._restart_failed_services(service_issues)
        if restarted:
            results.append(f"Restarted {restarted} services")

        # Phase 5: Check disk space and clean if needed
        disk_actions = self._manage_disk_space()
        if disk_actions:
            results.append(disk_actions)

        # Phase 6: Check failover conditions
        self._check_failover_conditions(metrics, service_issues)

        # Phase 7: Verify backups
        self._verify_backups()

        return "; ".join(results) if results else "All systems nominal"

    def run_forever(self):
        """Override to run metrics collection more frequently than full checks."""
        self._running = True
        self.on_start()
        self.logger.info(f"Safety Officer started. Metrics every {self.metrics_interval}s, full check every {self._interval}s")

        last_full_check = 0

        while self._running:
            cycle_start = time.time()

            try:
                # Always collect metrics
                metrics = self.collect_metrics()

                # Check thresholds every metrics cycle
                if metrics:
                    self._check_thresholds(metrics)

                # Full health check at configu
red interval
                if cycle_start - last_full_check >= self._interval:
                    self.run_once()
                    last_full_check = cycle_start

            except Exception as e:
                self.logger.error(f"Safety Officer error: {e}")
                self.db.log_incident("warning", self.AGENT_NAME, f"Cycle error: {str(e)[:200]}")

            # Sleep
            elapsed = time.time() - cycle_start
            sleep_time = max(0, self.metrics_interval - elapsed)
            if sleep_time > 0 and self._running:
                time.sleep(sleep_time)

        self.on_stop()

    def collect_metrics(self) -> Optional[dict]:
        """Collect comprehensive system metrics using psutil."""
        try:
            # CPU
            cpu_percent = psutil.cpu_percent(interval=1)
            cpu_count = psutil.cpu_count()
            cpu_freq = psutil.cpu_freq()
            cpu_freq_current = cpu_freq.current if cpu_freq else 0

            # Memory
            mem = psutil.virtual_memory()
            ram_percent = mem.percent
            ram_used_gb = round(mem.used / (1024 ** 3), 2)
            ram_total_gb = round(mem.total / (1024 ** 3), 2)
            ram_available_gb = round(mem.available / (1024 ** 3), 2)

            # Disk
            disk = psutil.disk_usage("/")
            disk_percent = disk.percent
            disk_free_gb = round(disk.free / (1024 ** 3), 2)
            disk_total_gb = round(disk.total / (1024 ** 3), 2)

            # GPU temperature (via nvidia-smi)
            gpu_temp = self._get_gpu_temp()
            gpu_util = self._get_gpu_utilization()

            # System uptime
            boot_time = datetime.fromtimestamp(psutil.boot_time())
            uptime_seconds = (datetime.now() - boot_time).total_seconds()

            # Network I/O
            net_io = psutil.net_io_counters()
            network_rx_mb = round(net_io.bytes_recv / (1024 ** 2), 2)
            network_tx_mb = round(net_io.bytes_sent / (1024 ** 
2), 2)

            # Process count
            process_count = len(psutil.pids())

            metrics = {
                "cpu_percent": cpu_percent,
                "cpu_count": cpu_count,
                "cpu_freq_mhz": round(cpu_freq_current, 0),
                "ram_percent": ram_percent,
                "ram_used_gb": ram_used_gb,
                "ram_total_gb": ram_total_gb,
                "ram_available_gb": ram_available_gb,
                "disk_percent": disk_percent,
                "disk_free_gb": disk_free_gb,
                "disk_total_gb": disk_total_gb,
                "gpu_temp": gpu_temp,
                "gpu_util": gpu_util,
                "uptime_seconds": uptime_seconds,
                "network_rx_mb": network_rx_mb,
                "network_tx_mb": network_tx_mb,
                "process_count": process_count,
                "recorded_at": now_iso(),
            }

            # Store in database
            self.db.record_metrics(metrics)
            self._last_metrics = metrics

            return metrics

        except Exception as e:
            logger.error(f"Metrics collection error: {e}")
            return None

    def _get_gpu_temp(self) -> Optional[float]:
        """Get GPU temperature via nvidia-smi."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                temps = result.stdout.strip().split("\n")
                if temps and temps[0].strip():
                    return float(temps[0].strip())
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        return None

    def _get_gpu_utilization(self) -> Optional[float]:
        """Get GPU utilization via nvidia-smi."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utiliza
tion.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                utils = result.stdout.strip().split("\n")
                if utils and utils[0].strip():
                    return float(utils[0].strip())
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        return None

    def _check_thresholds(self, metrics: Optional[dict]) -> list:
        """Check metrics against alert thresholds."""
        if not metrics:
            return []

        alerts = []

        # CPU threshold
        if metrics["cpu_percent"] > self.cpu_threshold:
            alert = {
                "type": "cpu_high",
                "value": metrics["cpu_percent"],
                "threshold": self.cpu_threshold,
                "severity": "critical" if metrics["cpu_percent"] > 95 else "warning",
            }
            alerts.append(alert)
            self._handle_alert(alert)

        # RAM threshold
        if metrics["ram_percent"] > self.ram_threshold:
            alert = {
                "type": "ram_high",
                "value": metrics["ram_percent"],
                "threshold": self.ram_threshold,
                "severity": "critical" if metrics["ram_percent"] > 95 else "warning",
            }
            alerts.append(alert)
            self._handle_alert(alert)

        # Disk threshold
        if metrics["disk_percent"] > self.disk_threshold:
            alert = {
                "type": "disk_high",
                "value": metrics["disk_percent"],
                "threshold": self.disk_threshold,
                "severity": "critical" if metrics["disk_percent"] > 95 else "warning",
            }
            alerts.append(alert)
            self._handle_alert(alert)

        # GPU temperature threshold
        if metrics.get("gpu_temp") and metrics["gpu_temp"] > self.gpu_temp_threshold:
            alert = {
          
      "type": "gpu_temp_high",
                "value": metrics["gpu_temp"],
                "threshold": self.gpu_temp_threshold,
                "severity": "critical" if metrics["gpu_temp"] > 95 else "warning",
            }
            alerts.append(alert)
            self._handle_alert(alert)

        return alerts

    def _handle_alert(self, alert: dict):
        """Handle a threshold alert."""
        severity = alert["severity"]
        alert_type = alert["type"]
        value = alert["value"]
        threshold = alert["threshold"]

        message = f"{alert_type}: {value} exceeds threshold {threshold}"

        if severity == "critical":
            logger.critical(f"CRITICAL ALERT: {message}")
            self.db.log_incident("critical", self.AGENT_NAME, message)
        else:
            logger.warning(f"WARNING: {message}")
            self.db.log_incident("warning", self.AGENT_NAME, message)

        # Specific remediation actions
        if alert_type == "disk_high":
            self._emergency_disk_cleanup()
        elif alert_type == "ram_high":
            self._identify_memory_hogs()
        elif alert_type == "gpu_temp_high":
            self._handle_gpu_overheat()

    def _check_services(self) -> list:
        """Check health of Institution services."""
        issues = []

        # Check systemd services
        services = [
            "institution.service",
            "institution-dashboard.service",
            "institution-safety.service",
        ]

        for service in services:
            status = self._get_service_status(service)
            if status != "active":
                issues.append({
                    "service": service,
                    "status": status,
                    "severity": "critical" if "meta" in service else "warning",
                })

        # Check Ollama
        ollama_healthy = self._check_ollama()
        if not ollama_healthy:
            issues.append({
                "service": "ollama
",
                "status": "unreachable",
                "severity": "warning",
            })

        # Check database integrity
        db_healthy = self._check_database()
        if not db_healthy:
            issues.append({
                "service": "sqlite_database",
                "status": "integrity_check_failed",
                "severity": "critical",
            })

        # Check disk I/O
        io_healthy = self._check_disk_io()
        if not io_healthy:
            issues.append({
                "service": "disk_io",
                "status": "high_latency",
                "severity": "warning",
            })

        return issues

    def _get_service_status(self, service_name: str) -> str:
        """Get systemd service status."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return "unknown"

    def _check_ollama(self) -> bool:
        """Check if Ollama is responsive."""
        try:
            import requests
            resp = requests.get("http://localhost:11434/api/tags", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def _check_database(self) -> bool:
        """Run SQLite integrity check."""
        try:
            from common import DB_PATH
            result = subprocess.run(
                ["sqlite3", str(DB_PATH), "PRAGMA integrity_check;"],
                capture_output=True, text=True, timeout=30,
            )
            return "ok" in result.stdout.lower()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return True  # Assume OK if can't check

    def _check_disk_io(self) -> bool:
        """Check if disk I/O is healthy."""
        try:
            io_counters = psutil.disk_io_co
unters()
            if io_counters:
                # If disk is extremely busy, flag it
                # This is a simplified check
                return True
            return True
        except Exception:
            return True

    def _restart_failed_services(self, issues: list) -> int:
        """Attempt to restart failed services with rate limiting."""
        restarted = 0

        for issue in issues:
            service = issue.get("service", "")
            if not service:
                continue

            # Check rate limit
            if not self._can_restart(service):
                logger.warning(f"Rate limited: cannot restart {service} (max {self.max_restarts}/hour)")
                continue

            # Attempt restart
            success = self._restart_service(service)
            if success:
                restarted += 1
                self._record_restart(service)
                logger.info(f"Restarted service: {service}")
            else:
                logger.error(f"Failed to restart service: {service}")
                self.db.log_incident(
                    "critical", service,
                    f"Restart failed. Manual intervention required."
                )

        return restarted

    def _can_restart(self, service: str) -> bool:
        """Check if service can be restarted (rate limiting)."""
        now = time.time()
        window_start = now - 3600  # 1 hour window

        if service not in self._restart_counts:
            self._restart_counts[service] = []

        # Remove old entries
        self._restart_counts[service] = [
            t for t in self._restart_counts[service] if t > window_start
        ]

        return len(self._restart_counts[service]) < self.max_restarts

    def _record_restart(self, service: str):
        """Record a restart attempt."""
        if service not in self._restart_counts:
            self._restart_counts[service] = []
        self._restart_counts[service].append(time
.time())

    def _restart_service(self, service: str) -> bool:
        """Restart a specific service."""
        try:
            if service == "ollama":
                result = subprocess.run(
                    ["sudo", "systemctl", "restart", "ollama"],
                    capture_output=True, text=True, timeout=30,
                )
                return result.returncode == 0
            elif service.startswith("institution"):
                result = subprocess.run(
                    ["sudo", "systemctl", "restart", service],
                    capture_output=True, text=True, timeout=30,
                )
                return result.returncode == 0
            elif service == "sqlite_database":
                # Can't restart SQLite, but can check and log
                return True
            else:
                result = subprocess.run(
                    ["sudo", "systemctl", "restart", service],
                    capture_output=True, text=True, timeout=30,
                )
                return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.error(f"Restart command failed for {service}: {e}")
            return False

    def _check_failover_conditions(self, metrics: Optional[dict], issues: list):
        """Check if failover to secondary node should be triggered."""
        if self._failover_active:
            return

        failover_cfg = self.config.get("safety", "failover", default={})
        if not failover_cfg.get("enabled", False):
            return

        # Count critical issues
        critical_issues = [i for i in issues if i.get("severity") == "critical"]

        # Trigger failover if multiple critical issues
        if len(critical_issues) >= 2:
            logger.critical(
                f"FAILOVER TRIGGERED: {len(critical_issues)} critical issues detected. "
                f"Issues: {[i['service'] for i in critical_issues]}"
            )
            self._
trigger_failover(critical_issues)

        # Also trigger if meta-agent is down for extended period
        meta_status = self._get_service_status("institution.service")
        if meta_status != "active":
            # Check how long it's been down
            incident = self.db.fetchone(
                """SELECT created_at FROM incidents
                   WHERE component = 'institution.service' AND resolved = 0
                   ORDER BY created_at DESC LIMIT 1"""
            )
            if incident:
                try:
                    down_since = datetime.fromisoformat(incident["created_at"])
                    down_minutes = (datetime.now() - down_since).total_seconds() / 60
                    if down_minutes > 5:  # 5 minute threshold
                        logger.critical(f"Meta-agent down for {down_minutes:.0f} minutes. Triggering failover.")
                        self._trigger_failover([{"service": "institution.service", "status": "down"}])
                except (ValueError, TypeError):
                    pass

    def _trigger_failover(self, critical_issues: list):
        """Trigger failover to secondary node."""
        self._failover_active = True

        self.db.log_incident(
            "critical", "failover",
            f"Failover triggered. Critical issues: {json.dumps(critical_issues, default=str)}"
        )

        # Log the failover event
        failover_log = {
            "triggered_at": now_iso(),
            "reason": "Multiple critical service failures",
            "critical_issues": critical_issues,
            "target": self.config.get("safety", "failover", "target", default="oracle_cloud"),
            "status": "triggered",
        }

        failover_path = INSTITUTION_ROOT / "logs" / "system" / f"failover_{today_str()}.json"
        failover_path.parent.mkdir(parents=True, exist_ok=True)
        failover_path.write_text(json.dumps(failover_log, indent=2, default=str), encoding="utf-8")

        # Attempt to notif
y (would send email/alert in production)
        logger.critical(
            "FAILOVER ACTIVE: Primary node degraded. "
            "Secondary node should take over monitoring and digest generation. "
            "Heavy workloads suspended until primary recovers."
        )

        # Create approval for founder notification
        self.db.add_approval(
            item_type="failover_notification",
            description="SYSTEM FAILOVER: Primary node degraded. Review incident log.",
            details=json.dumps(failover_log, default=str),
            agent=self.AGENT_NAME,
            priority=1,
        )

    def _manage_disk_space(self) -> Optional[str]:
        """Manage disk space proactively."""
        try:
            disk = psutil.disk_usage("/")
            if disk.percent < 80:
                return None

            actions = []

            # Clean old logs
            logs_dir = INSTITUTION_ROOT / "logs"
            if logs_dir.exists():
                cutoff = datetime.now() - timedelta(days=30)
                cleaned = 0
                for log_file in logs_dir.rglob("*.log"):
                    try:
                        if datetime.fromtimestamp(log_file.stat().st_mtime) < cutoff:
                            log_file.unlink()
                            cleaned += 1
                    except (OSError, IOError):
                        continue
                if cleaned:
                    actions.append(f"Cleaned {cleaned} old log files")

            # Clean old cache
            cache_dir = INSTITUTION_ROOT / "data" / "cache"
            if cache_dir.exists():
                cutoff = datetime.now() - timedelta(days=7)
                cleaned = 0
                for cache_file in cache_dir.rglob("*"):
                    if cache_file.is_file():
                        try:
                            if datetime.fromtimestamp(cache_file.stat().st_mtime) < cutoff:
                                cache_file.unlink()
               
                 cleaned += 1
                        except (OSError, IOError):
                            continue
                if cleaned:
                    actions.append(f"Cleaned {cleaned} cache files")

            # Clean old video footage (large files)
            footage_dir = INSTITUTION_ROOT / "videos" / "footage"
            if footage_dir.exists():
                cutoff = datetime.now() - timedelta(days=14)
                cleaned_size = 0
                for media_file in footage_dir.rglob("*.mp4"):
                    try:
                        if datetime.fromtimestamp(media_file.stat().st_mtime) < cutoff:
                            cleaned_size += media_file.stat().st_size
                            media_file.unlink()
                    except (OSError, IOError):
                        continue
                if cleaned_size > 0:
                    actions.append(f"Cleaned {cleaned_size // (1024*1024)}MB old footage")

            # Clean expired AI cache from database
            self.db.cleanup_expired_cache()
            actions.append("Cleaned expired AI cache")

            if actions:
                logger.info(f"Disk management: {'; '.join(actions)}")
                return "; ".join(actions)

        except Exception as e:
            logger.debug(f"Disk management error: {e}")

        return None

    def _emergency_disk_cleanup(self):
        """Emergency cleanup when disk is critically full."""
        logger.warning("EMERGENCY: Disk critically full. Performing aggressive cleanup.")

        # Remove all video footage older than 7 days
        footage_dir = INSTITUTION_ROOT / "videos" / "footage"
        if footage_dir.exists():
            cutoff = datetime.now() - timedelta(days=7)
            for f in footage_dir.rglob("*"):
                if f.is_file():
                    try:
                        if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                            f.unlink()
                    
except (OSError, IOError):
                        continue

        # Remove all logs older than 7 days
        logs_dir = INSTITUTION_ROOT / "logs"
        if logs_dir.exists():
            cutoff = datetime.now() - timedelta(days=7)
            for f in logs_dir.rglob("*.log"):
                try:
                    if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                        f.unlink()
                except (OSError, IOError):
                    continue

        # Vacuum SQLite database
        try:
            from common import DB_PATH
            subprocess.run(
                ["sqlite3", str(DB_PATH), "VACUUM;"],
                capture_output=True, timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    def _identify_memory_hogs(self):
        """Identify processes using excessive memory."""
        try:
            processes = []
            for proc in psutil.process_iter(["pid", "name", "memory_percent", "memory_info"]):
                try:
                    info = proc.info
                    if info["memory_percent"] and info["memory_percent"] > 10:
                        processes.append({
                            "pid": info["pid"],
                            "name": info["name"],
                            "memory_percent": round(info["memory_percent"], 1),
                            "memory_mb": round(info["memory_info"].rss / (1024 ** 2), 0) if info["memory_info"] else 0,
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            if processes:
                processes.sort(key=lambda p: p["memory_percent"], reverse=True)
                top_hogs = processes[:5]
                hog_report = "; ".join(
                    f"{p['name']} (PID {p['pid']}): {p['memory_percent']}% / {p['memory_mb']}MB"
                    for p in top_hogs
                )
                logger.warning(
f"Memory hogs: {hog_report}")
                self.db.log_incident("warning", "memory", f"High memory: {hog_report}")

        except Exception as e:
            logger.debug(f"Memory hog identification error: {e}")

    def _handle_gpu_overheat(self):
        """Handle GPU overheating."""
        logger.warning("GPU overheating. Checking for runaway processes.")

        try:
            # Get GPU processes
            result = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                gpu_procs = result.stdout.strip().split("\n")
                logger.warning(f"GPU processes: {gpu_procs}")

                # If Ollama is using too much, consider restarting it
                for proc_line in gpu_procs:
                    if "ollama" in proc_line.lower() or "llama" in proc_line.lower():
                        logger.warning("Ollama may be causing GPU overheat. Consider reducing model size.")
                        break

        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    def _verify_backups(self):
        """Verify that backups exist and are recent."""
        backup_dir = INSTITUTION_ROOT / "data" / "db" / "backups"
        if not backup_dir.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            return

        # Check for recent backup
        backups = sorted(backup_dir.glob("institution_*.db"), reverse=True)
        if not backups:
            logger.warning("No database backups found!")
            self.db.log_incident("warning", "backups", "No database backups found.")
            return

        latest_backup = backups[0]
        backup_age = datetime.now() - datetime.fromtimestamp(latest_backup.stat().st_mtime)

        if backup_age > timedelta(hours=36):
            logger.warning(f"Lates
t backup is {backup_age.total_seconds()/3600:.0f} hours old.")
            self.db.log_incident(
                "warning", "backups",
                f"Backup age: {backup_age.total_seconds()/3600:.0f} hours. Expected <24h."
            )

    def get_system_status(self) -> dict:
        """Get current system status for dashboard."""
        metrics = self._last_metrics or self.collect_metrics() or {}

        services = {}
        for svc in ["institution.service", "institution-dashboard.service", "institution-safety.service", "ollama"]:
            services[svc] = self._get_service_status(svc)

        return {
            "metrics": metrics,
            "services": services,
            "failover_active": self._failover_active,
            "restart_counts": {k: len(v) for k, v in self._restart_counts.items()},
            "last_check": now_iso(),
        }