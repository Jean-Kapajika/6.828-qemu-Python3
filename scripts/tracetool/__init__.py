#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Machinery for generating tracing-related intermediate files.
"""

__author__     = "Lluís Vilanova <vilanova@ac.upc.edu>"
__copyright__  = "Copyright 2012-2014, Lluís Vilanova <vilanova@ac.upc.edu>"
__license__    = "GPL version 2 or (at your option) any later version"

__maintainer__ = "Stefan Hajnoczi"
__email__      = "stefanha@linux.vnet.ibm.com"


import re
import sys
import weakref

import tracetool.format
import tracetool.backend
import tracetool.transform


def error_write(*lines):
    """Write a set of error lines."""
    sys.stderr.writelines("\n".join(lines) + "\n")

def error(*lines):
    """Write a set of error lines and exit."""
    error_write(*lines)
    sys.exit(1)


def out(*lines, **kwargs):
    """Write a set of output lines.

    You can use kwargs as a shorthand for mapping variables when formating all
    the strings in lines.
    """
    lines = [ l % kwargs for l in lines ]
    sys.stdout.writelines("\n".join(lines) + "\n")


class Arguments:
    """Event arguments description."""

    def __init__(self, args):
        """
        Parameters
        ----------
        args :
            List of (type, name) tuples.
        """
        self._args = args

    def copy(self):
        """Create a new copy."""
        return Arguments(list(self._args))

    @staticmethod
    def build(arg_str):
        """Build and Arguments instance from an argument string.

        Parameters
        ----------
        arg_str : str
            String describing the event arguments.
        """
        res = []
        for arg in arg_str.split(","):
            arg = arg.strip()
            if arg == 'void':
                continue

            if '*' in arg:
                arg_type, identifier = arg.rsplit('*', 1)
                arg_type += '*'
                identifier = identifier.strip()
            else:
                arg_type, identifier = arg.rsplit(None, 1)

            res.append((arg_type, identifier))
        return Arguments(res)

    def __iter__(self):
        """Iterate over the (type, name) pairs."""
        return iter(self._args)

    def __len__(self):
        """Number of arguments."""
        return len(self._args)

    def __str__(self):
        """String suitable for declaring function arguments."""
        if len(self._args) == 0:
            return "void"
        else:
            return ", ".join([ " ".join([t, n]) for t,n in self._args ])

    def __repr__(self):
        """Evaluable string representation for this object."""
        return "Arguments(\"%s\")" % str(self)

    def names(self):
        """List of argument names."""
        return [ name for _, name in self._args ]

    def types(self):
        """List of argument types."""
        return [ type_ for type_, _ in self._args ]

    def transform(self, *trans):
        """Return a new Arguments instance with transformed types.

        The types in the resulting Arguments instance are transformed according
        to tracetool.transform.transform_type.
        """
        res = []
        for type_, name in self._args:
            res.append((tracetool.transform.transform_type(type_, *trans),
                        name))
        return Arguments(res)


class Event(object):
    """Event description.

    Attributes
    ----------
    name : str
        The event name.
    fmt : str
        The event format string.
    properties : set(str)
        Properties of the event.
    args : Arguments
        The event arguments.

    """

    _CRE = re.compile("((?P<props>[\w\s]+)\s+)?"
                      "(?P<name>\w+)"
                      "\((?P<args>[^)]*)\)"
                      "\s*"
                      "(?:(?:(?P<fmt_trans>\".+),)?\s*(?P<fmt>\".+))?"
                      "\s*")

    _VALID_PROPS = set(["disable", "tcg", "tcg-trans", "tcg-exec"])

    def __init__(self, name, props, fmt, args, orig=None):
        """
        Parameters
        ----------
        name : string
            Event name.
        props : list of str
            Property names.
        fmt : str, list of str
            Event printing format (or formats).
        args : Arguments
            Event arguments.
        orig : Event or None
            Original Event before transformation.

        """
        self.name = name
        self.properties = props
        self.fmt = fmt
        self.args = args

        if orig is None:
            self.original = weakref.ref(self)
        else:
            self.original = orig

        unknown_props = set(self.properties) - self._VALID_PROPS
        if len(unknown_props) > 0:
            raise ValueError("Unknown properties: %s"
                             % ", ".join(unknown_props))
        assert isinstance(self.fmt, str) or len(self.fmt) == 2

    def copy(self):
        """Create a new copy."""
        return Event(self.name, list(self.properties), self.fmt,
                     self.args.copy(), self)

    @staticmethod
    def build(line_str):
        """Build an Event instance from a string.

        Parameters
        ----------
        line_str : str
            Line describing the event.
        """
        m = Event._CRE.match(line_str)
        assert m is not None
        groups = m.groupdict('')

        name = groups["name"]
        props = groups["props"].split()
        fmt = groups["fmt"]
        fmt_trans = groups["fmt_trans"]
        if len(fmt_trans) > 0:
            fmt = [fmt_trans, fmt]
        args = Arguments.build(groups["args"])

        if "tcg-trans" in props:
            raise ValueError("Invalid property 'tcg-trans'")
        if "tcg-exec" in props:
            raise ValueError("Invalid property 'tcg-exec'")
        if "tcg" not in props and not isinstance(fmt, str):
            raise ValueError("Only events with 'tcg' property can have two formats")
        if "tcg" in props and isinstance(fmt, str):
            raise ValueError("Events with 'tcg' property must have two formats")

        return Event(name, props, fmt, args)

    def __repr__(self):
        """Evaluable string representation for this object."""
        if isinstance(self.fmt, str):
            fmt = self.fmt
        else:
            fmt = "%s, %s" % (self.fmt[0], self.fmt[1])
        return "Event('%s %s(%s) %s')" % (" ".join(self.properties),
                                          self.name,
                                          self.args,
                                          fmt)

    _FMT = re.compile("(%[\d\.]*\w+|%.*PRI\S+)")

    def formats(self):
        """List of argument print formats."""
        assert not isinstance(self.fmt, list)
        return self._FMT.findall(self.fmt)

    QEMU_TRACE               = "trace_%(name)s"
    QEMU_TRACE_TCG           = QEMU_TRACE + "_tcg"

    def api(self, fmt=None):
        if fmt is None:
            fmt = Event.QEMU_TRACE
        return fmt % {"name": self.name}

    def transform(self, *trans):
        """Return a new Event with transformed Arguments."""
        return Event(self.name,
                     list(self.properties),
                     self.fmt,
                     self.args.transform(*trans),
                     self)


def _read_events(fobj):
    events = []
    for line in fobj:
        if not line.strip():
            continue
        if line.lstrip().startswith('#'):
            continue

        event = Event.build(line)

        # transform TCG-enabled events
        if "tcg" not in event.properties:
            events.append(event)
        else:
            event_trans = event.copy()
            event_trans.name += "_trans"
            event_trans.properties += ["tcg-trans"]
            event_trans.fmt = event.fmt[0]
            args_trans = []
            for atrans, aorig in zip(
                    event_trans.transform(tracetool.transform.TCG_2_HOST).args,
                    event.args):
                if atrans == aorig:
                    args_trans.append(atrans)
            event_trans.args = Arguments(args_trans)
            event_trans = event_trans.copy()

            event_exec = event.copy()
            event_exec.name += "_exec"
            event_exec.properties += ["tcg-exec"]
            event_exec.fmt = event.fmt[1]
            event_exec = event_exec.transform(tracetool.transform.TCG_2_HOST)

            new_event = [event_trans, event_exec]
            event.event_trans, event.event_exec = new_event

            events.extend(new_event)

    return events


class TracetoolError (Exception):
    """Exception for calls to generate."""
    pass


def try_import(mod_name, attr_name=None, attr_default=None):
    """Try to import a module and get an attribute from it.

    Parameters
    ----------
    mod_name : str
        Module name.
    attr_name : str, optional
        Name of an attribute in the module.
    attr_default : optional
        Default value if the attribute does not exist in the module.

    Returns
    -------
    A pair indicating whether the module could be imported and the module or
    object or attribute value.
    """
    try:
        module = __import__(mod_name, globals(), locals(), ["__package__"])
        if attr_name is None:
            return True, module
        return True, getattr(module, str(attr_name), attr_default)
    except ImportError:
        return False, None


def generate(fevents, format, backends,
             binary=None, probe_prefix=None):
    """Generate the output for the given (format, backends) pair.

    Parameters
    ----------
    fevents : file
        Event description file.
    format : str
        Output format name.
    backends : list
        Output backend names.
    binary : str or None
        See tracetool.backend.dtrace.BINARY.
    probe_prefix : str or None
        See tracetool.backend.dtrace.PROBEPREFIX.
    """
    # fix strange python error (UnboundLocalError tracetool)
    import tracetool

    format = str(format)
    if len(format) == 0:
        raise TracetoolError("format not set")
    if not tracetool.format.exists(format):
        raise TracetoolError("unknown format: %s" % format)

    if len(backends) == 0:
        raise TracetoolError("no backends specified")
    for backend in backends:
        if not tracetool.backend.exists(backend):
            raise TracetoolError("unknown backend: %s" % backend)
    backend = tracetool.backend.Wrapper(backends, format)

    import tracetool.backend.dtrace
    tracetool.backend.dtrace.BINARY = binary
    tracetool.backend.dtrace.PROBEPREFIX = probe_prefix

    events = _read_events(fevents)

    tracetool.format.generate(events, format, backend)
