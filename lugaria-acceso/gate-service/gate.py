"""
Controladores de la pluma.

Casi todas las plumas comerciales (ZKTeco, CAME, FAAC, Accesspro, etc.)
tienen una entrada de "contacto seco" para abrir: al cerrar ese contacto
un instante, la pluma sube. Aquí simulamos ese botón con un relevador.

Controladores disponibles (gate.driver en config.yaml):
  simulado  -> sólo imprime en consola (para pruebas)
  gpio      -> relevador conectado a un pin GPIO de Raspberry Pi
  http      -> relevador WiFi (Shelly, Sonoff con Tasmota, ESP32) o la
               API HTTP del propio controlador de la pluma
  serial    -> Arduino / módulo USB que recibe un comando por puerto serie
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("pluma")


class Gate:
    def __init__(self, cfg: dict):
        self.pulse_s = float(cfg.get("pulse_ms", 800)) / 1000
        self._lock = threading.Lock()

    def open(self, why: str = "") -> bool:
        """Pulsa el relevador. Devuelve True si se envió la orden."""
        if not self._lock.acquire(blocking=False):
            log.info("Pluma ya en movimiento, se ignora orden duplicada")
            return True
        try:
            log.info("ABRIR PLUMA (%s)", why)
            self._pulse()
            return True
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo abrir la pluma: %s", e)
            return False
        finally:
            self._lock.release()

    def _pulse(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class SimulatedGate(Gate):
    def _pulse(self) -> None:
        print("\n   ▲▲▲  PLUMA ARRIBA (simulado)  ▲▲▲\n", flush=True)
        time.sleep(self.pulse_s)


class GpioGate(Gate):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        from gpiozero import OutputDevice  # sólo existe en Raspberry Pi
        self.dev = OutputDevice(int(cfg.get("gpio_pin", 17)),
                                active_high=bool(cfg.get("active_high", True)),
                                initial_value=False)

    def _pulse(self) -> None:
        self.dev.on()
        time.sleep(self.pulse_s)
        self.dev.off()

    def close(self) -> None:
        self.dev.close()


class HttpGate(Gate):
    """
    Ejemplo Shelly 1 (Gen1):  http://192.168.1.50/relay/0?turn=on&timer=1
    Ejemplo Shelly Plus/Gen2: http://192.168.1.50/rpc/Switch.Set?id=0&on=true&toggle_after=1
    Si el dispositivo no se apaga solo, configura http_url_off.
    """
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        import requests
        self.requests = requests
        self.url_on = cfg["http_url"]
        self.url_off = cfg.get("http_url_off")
        self.method = cfg.get("http_method", "GET").upper()
        self.auth = tuple(cfg["http_auth"]) if cfg.get("http_auth") else None
        self.timeout = float(cfg.get("http_timeout", 3))

    def _call(self, url: str) -> None:
        r = self.requests.request(self.method, url, auth=self.auth, timeout=self.timeout)
        r.raise_for_status()

    def _pulse(self) -> None:
        self._call(self.url_on)
        if self.url_off:
            time.sleep(self.pulse_s)
            self._call(self.url_off)


class SerialGate(Gate):
    """Envía p. ej. 'OPEN\\n' a un Arduino que activa el relevador."""
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        import serial
        self.port = serial.Serial(cfg.get("serial_port", "/dev/ttyUSB0"),
                                  int(cfg.get("serial_baud", 9600)), timeout=1)
        self.cmd = cfg.get("serial_command", "OPEN\n").encode()

    def _pulse(self) -> None:
        self.port.write(self.cmd)
        self.port.flush()

    def close(self) -> None:
        self.port.close()


DRIVERS = {"simulado": SimulatedGate, "gpio": GpioGate, "http": HttpGate, "serial": SerialGate}


def make_gate(cfg: dict) -> Gate:
    name = cfg.get("driver", "simulado")
    if name not in DRIVERS:
        raise ValueError(f"gate.driver desconocido: {name}. Opciones: {', '.join(DRIVERS)}")
    return DRIVERS[name](cfg)
