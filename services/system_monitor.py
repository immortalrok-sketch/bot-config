import time
import threading
import psutil

# Пробуем импортировать pynvml для видеокарт NVIDIA
try:
    import pynvml
    HAS_PYNVML = True
except ImportError:
    HAS_PYNVML = False

# Пробуем импортировать wmi для чтения датчиков температуры
try:
    import wmi
    HAS_WMI = True
except ImportError:
    HAS_WMI = False

class SystemMonitor:
    """
    Модуль фонового мониторинга ресурсов компьютера:
    температура CPU/GPU, загрузка CPU, VRAM, RAM и детальная информация о диске.
    """
    def __init__(self, dashboard=None, interval_sec=3, warn_gpu_temp=75, crit_gpu_temp=85):
        self.dashboard = dashboard
        self.interval_sec = interval_sec
        self.warn_gpu_temp = warn_gpu_temp
        self.crit_gpu_temp = crit_gpu_temp
        self.is_running = False
        self.thread = None
        self.tracked_windows = []

        if HAS_PYNVML:
            try:
                pynvml.nvmlInit()
            except Exception:
                pass

    def set_tracked_windows(self, windows):
        self.tracked_windows = windows

    def get_cpu_temp(self):
        """Точный опрос температуры CPU через WMI-пространство OpenHardwareMonitor."""
        if not HAS_WMI:
            return None

        # Опрос датчиков через OpenHardwareMonitor
        try:
            w_ohm = wmi.WMI(namespace="root\\OpenHardwareMonitor")
            sensors = w_ohm.Sensor()
            for sensor in sensors:
                # Проверяем, что это датчик температуры и он относится к процессору
                if getattr(sensor, 'SensorType', '') == 'Temperature':
                    name = getattr(sensor, 'Name', '')
                    if 'CPU' in name or 'Package' in name:
                        if sensor.Value is not None:
                            return round(float(sensor.Value), 1)
        except Exception:
            pass

        # Запасной вариант через стандартный ACPI
        try:
            w = wmi.WMI(namespace="root\\wmi")
            temperature_info = w.MSAcpi_ThermalZoneTemperature()
            if temperature_info:
                temp_c = temperature_info[0].CurrentTemperature / 10.0 - 273.15
                if 0 < temp_c < 110:
                    return round(temp_c, 1)
        except Exception:
            pass

        return None

    def get_system_metrics(self):
        """Сбор всех метрик системы."""
        cpu_percent = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory()
        ram_used_gb = round(ram.used / (1024**3), 1)
        ram_total_gb = round(ram.total / (1024**3), 1)

        # Получаем температуру процессора
        cpu_temp = self.get_cpu_temp()

        # Данные по диску C: (Занято / Свободно / Всего)
        try:
            disk = psutil.disk_usage('C:\\')
            disk_used_gb = round(disk.used / (1024**3), 1)
            disk_free_gb = round(disk.free / (1024**3), 1)
            disk_total_gb = round(disk.total / (1024**3), 1)
        except Exception:
            disk_used_gb = None
            disk_free_gb = None
            disk_total_gb = None

        gpu_temp = None
        gpu_load = None
        vram_used_gb = None
        vram_total_gb = None

        if HAS_PYNVML:
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                gpu_temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                
                rates = pynvml.nvmlDeviceGetUtilizationRates(handle)
                gpu_load = rates.gpu
                
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                vram_used_gb = round(mem_info.used / (1024**3), 1)
                vram_total_gb = round(mem_info.total / (1024**3), 1)
            except Exception:
                pass

        return {
            "cpu": cpu_percent,
            "cpu_temp": cpu_temp,
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
            "disk_used_gb": disk_used_gb,
            "disk_free_gb": disk_free_gb,
            "disk_total_gb": disk_total_gb,
            "gpu_temp": gpu_temp,
            "gpu_load": gpu_load,
            "vram_used_gb": vram_used_gb,
            "vram_total_gb": vram_total_gb
        }

    def _monitoring_loop(self):
        while self.is_running:
            metrics = self.get_system_metrics()
            if self.dashboard:
                self.dashboard.send_system_stats(metrics)
            time.sleep(self.interval_sec)

    def start(self):
        if not self.is_running:
            self.is_running = True
            self.thread = threading.Thread(target=self._monitoring_loop, daemon=True)
            self.thread.start()

    def stop(self):
        self.is_running = False