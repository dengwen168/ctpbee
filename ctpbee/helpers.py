""" 工具函数 """

import os
import pkgutil
import sys
import types
from datetime import datetime, time
from functools import wraps
from threading import RLock
from time import sleep
from typing import AnyStr, Tuple, IO

from ctpbee.trade_time import TradingDay

_missing = object()


class locked_cached_property(object):
    def __init__(self, func, name=None, doc=None):
        self.__name__ = name or func.__name__
        self.__module__ = func.__module__
        self.__doc__ = doc or func.__doc__
        self.func = func
        self.lock = RLock()

    def __get__(self, obj, type=None):
        if obj is None:
            return self
        with self.lock:
            value = obj.__dict__.get(self.__name__, _missing)
            if value is _missing:
                value = self.func(obj)
                obj.__dict__[self.__name__] = value
            return value


def find_package(import_name):
    root_mod_name = import_name.split('.')[0]
    loader = pkgutil.get_loader(root_mod_name)
    if loader is None or import_name == '__main__':
        package_path = os.getcwd()
    else:
        if hasattr(loader, 'get_filename'):
            filename = loader.get_filename(root_mod_name)
        elif hasattr(loader, 'archive'):
            filename = loader.archive
        else:
            __import__(import_name)
            filename = sys.modules[import_name].__file__
        package_path = os.path.abspath(os.path.dirname(filename))
        if _matching_loader_thinks_module_is_package(
                loader, root_mod_name):
            package_path = os.path.dirname(package_path)

    site_parent, site_folder = os.path.split(package_path)
    py_prefix = os.path.abspath(sys.prefix)
    if package_path.startswith(py_prefix):
        return py_prefix, package_path
    elif site_folder.lower() == 'site-packages':
        parent, folder = os.path.split(site_parent)
        # Windows like installations
        if folder.lower() == 'lib':
            base_dir = parent
        elif os.path.basename(parent).lower() == 'lib':
            base_dir = os.path.dirname(parent)
        else:
            base_dir = site_parent
        return base_dir, package_path
    return None, package_path


def _matching_loader_thinks_module_is_package(loader, mod_name):
    if hasattr(loader, 'is_package'):
        return loader.is_package(mod_name)
    # importlib's namespace loaders do not have this functionality but
    # all the modules it loads are packages, so we can take advantage of
    # this information.
    elif (loader.__class__.__module__ == '_frozen_importlib' and
          loader.__class__.__name__ == 'NamespaceLoader'):
        return True
    # Otherwise we need to fail with an error that explains what went
    # wrong.
    raise AttributeError(
        ('%s.is_package() method is missing but is required by CtpBee of '
         'PEP 302 import hooks.  If you do not use import hooks and '
         'you encounter this error please file a bug against Flask.') %
        loader.__class__.__name__)


def check(type: AnyStr):
    """ 检查API是否存在 """

    def midlle(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if type == "market":
                if args[0].market is None:
                    raise ValueError("当前账户行情api未连接")
            elif type == "trader":
                if args[0].market is None:
                    raise ValueError("当前账户交易api未连接")
            else:
                raise ValueError("非法字符串")
            return func(*args, **kwargs)

        return wrapper

    return midlle


def dynamic_loading_api(f1):
    """
    f 是文件流
    主要是用来通过文件动态载入策略。 返回策略类的实例， 应该通过Ctpbee.add_extension() 加以载入
    你需要在策略代码文件中显式指定ext
    返回元组
    """
    if not isinstance(f, IO):
        raise ValueError(f"请确保你传入的是文件流(IO)，而不是{str(type(f))}")
    d = types.ModuleType("object")
    d.__file__ = f.name
    exec(compile(f.read(), f.name, 'exec'), d.__dict__)
    if not hasattr(d, "ext"):
        raise AttributeError("请检查你的策略中是否包含ext变量")
    if not isinstance(d.ext, Tuple):
        raise ValueError("错误变量")
    return d.ext


def auth_check_time(timed: datetime):
    """ 检查启动时间 """
    data_time = timed.time()
    if not isinstance(data_time, time):
        raise TypeError("参数类型错误, 期望为datatime.time}")
    DAY_START = time(8, 45)  # 日盘启动和停止时间
    DAY_END = time(15, 5)
    NIGHT_START = time(20, 45)  # 夜盘启动和停止时间
    NIGHT_END = time(2, 35)
    if data_time <= DAY_END and data_time >= DAY_START:
        return True
    if data_time >= NIGHT_START:
        return True
    if data_time <= NIGHT_END:
        return True
    return False


def run_forever(app):
    """
        永久运行函数 60秒检查一次
        非交易日不进行载入

    """

    running_me = False
    running_status = True

    while True:

        if not app.p_flag:
            break
        current_time = datetime.now()
        if TradingDay.is_holiday(current_time) or TradingDay.is_weekend(current_time):
            running_me = False
        else:
            running_me = True
        if auth_check_time(current_time):
            pass
        else:
            running_me = False
        if running_me and not running_status:
            """ 到了该启动的时间但是没运行 """
            app.reload()
            for x in app.extensions.keys():
                app.enable_extension(x)
            print(f"重新进行自动登录， 时间: {str(current_time)}")
            running_status = True

        elif running_me and running_status:
            """ 到了该启动的时间已经在运行 """

        elif not running_me and running_status:
            """ 非交易日 并且在运行 """
            for x in app.extensions.keys():
                app.suspend_extension(x)
            print(f"当前时间不允许， 时间: {str(current_time)}, 即将阻断运行")
            running_status = False

        elif not running_me and not running_status:
            """  非交易日 并且不运行 """

        sleep(1)


def refresh_query(app):
    """ 循环查询 """
    while True:
        sleep(1)
        cur = datetime.now()
        if not TradingDay.is_trading_day(cur) or not auth_check_time(cur):
            continue
        app.query_position()
        sleep(app.config['REFRESH_INTERVAL'])
        app.query_account()
        if not app.r_flag:
            break
