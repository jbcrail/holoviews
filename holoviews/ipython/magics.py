import time
import sys

try:
    from IPython.core.magic import Magics, magics_class, line_magic, line_cell_magic
except:
    from unittest import SkipTest
    raise SkipTest("IPython extension requires IPython >= 0.13")


from ..core import util
from ..core.options import Options, OptionError, Store, StoreOptions, options_policy
from ..core.pprint import InfoPrinter

from IPython.display import display, HTML
from ..operation import Compositor


#========#
# Magics #
#========#


try:
    import pyparsing
except ImportError:
    pyparsing = None
else:
    from holoviews.util.parser import CompositorSpec
    from holoviews.util.parser import OptsSpec


# Set to True to automatically run notebooks.
STORE_HISTORY = False

from IPython.core import page
InfoPrinter.store = Store


@magics_class
class OutputMagic(Magics):

    @line_cell_magic
    def output(self, line, cell=None):
        def cell_runner(cell):
            self.shell.run_cell(cell, store_history=STORE_HISTORY)
        Store.output_options.output(line, cell, cell_runner=cell_runner)

    @classmethod
    def option_completer(cls, k,v):
        raw_line = v.text_until_cursor
        line = raw_line.replace(Store.output_options.magic_name,'')

        # Find the last element class mentioned
        completion_key = None
        tokens = [t for els in reversed(line.split('=')) for t in els.split()]

        for token in tokens:
            if token.strip() in Store.output_options.allowed:
                completion_key = token.strip()
                break

        values = [val for val in Store.output_options.allowed.get(completion_key, [])
                  if val not in Store.output_options.hidden.get(completion_key, [])]
        vreprs = [repr(el) for el in values if not isinstance(el, tuple)]
        return vreprs + [el+'=' for el in Store.output_options.allowed.keys()]


@magics_class
class CompositorMagic(Magics):
    """
    Magic allowing easy definition of compositor operations.
    Consult %compositor? for more information.
    """

    def __init__(self, *args, **kwargs):
        super(CompositorMagic, self).__init__(*args, **kwargs)
        lines = ['The %compositor line magic is used to define compositors.']
        self.compositor.__func__.__doc__ = '\n'.join(lines + [CompositorSpec.__doc__])


    @line_magic
    def compositor(self, line):
        if line.strip():
            for definition in CompositorSpec.parse(line.strip(), ns=self.shell.user_ns):
                group = {'style':Options(), 'plot': Options(), 'norm':Options()}
                type_name = definition.output_type.__name__
                Store.options()[type_name + '.' + definition.group] = group
                Compositor.register(definition)
        else:
            print("For help with the %compositor magic, call %compositor?\n")


    @classmethod
    def option_completer(cls, k,v):
        line = v.text_until_cursor
        operation_openers = [op.__name__+'(' for op in Compositor.operations]

        modes = ['data', 'display']
        op_declared = any(op in line for op in operation_openers)
        mode_declared = any(mode in line for mode in modes)
        if not mode_declared:
            return modes
        elif not op_declared:
            return operation_openers
        if op_declared and ')' not in line:
            return [')']
        elif line.split(')')[1].strip() and ('[' not in line):
            return ['[']
        elif '[' in line:
            return [']']



class OptsCompleter(object):
    """
    Implements the TAB-completion for the %%opts magic.
    """
    _completions = {} # Contains valid plot and style keywords per Element

    @classmethod
    def setup_completer(cls):
        "Get the dictionary of valid completions"
        try:
            for element in Store.options().keys():
                options = Store.options()['.'.join(element)]
                plotkws = options['plot'].allowed_keywords
                stylekws = options['style'].allowed_keywords
                dotted = '.'.join(element)
                cls._completions[dotted] = (plotkws, stylekws if stylekws else [])
        except KeyError:
            pass
        return cls._completions

    @classmethod
    def dotted_completion(cls, line, sorted_keys, compositor_defs):
        """
        Supply the appropriate key in Store.options and supply
        suggestions for further completion.
        """
        completion_key, suggestions = None, []
        tokens = [t for t in reversed(line.replace('.', ' ').split())]
        for i, token in enumerate(tokens):
            key_checks =[]
            if i >= 0:  # Undotted key
                key_checks.append(tokens[i])
            if i >= 1:  # Single dotted key
                key_checks.append('.'.join([key_checks[-1], tokens[i-1]]))
            if i >= 2:  # Double dotted key
                key_checks.append('.'.join([key_checks[-1], tokens[i-2]]))
            # Check for longest potential dotted match first
            for key in reversed(key_checks):
                if key in sorted_keys:
                    completion_key = key
                    depth = completion_key.count('.')
                    suggestions = [k.split('.')[depth+1] for k in sorted_keys
                                   if k.startswith(completion_key+'.')]
                    return completion_key, suggestions
            # Attempting to match compositor definitions
            if token in compositor_defs:
                completion_key = compositor_defs[token]
                break
        return completion_key, suggestions

    @classmethod
    def _inside_delims(cls, line, opener, closer):
        return (line.count(opener) - line.count(closer)) % 2

    @classmethod
    def option_completer(cls, k,v):
        "Tab completion hook for the %%opts cell magic."
        line = v.text_until_cursor
        completions = cls.setup_completer()
        compositor_defs = {el.group:el.output_type.__name__
                           for el in Compositor.definitions}
        return cls.line_completer(line, completions, compositor_defs)

    @classmethod
    def line_completer(cls, line, completions, compositor_defs):
        sorted_keys = sorted(completions.keys())
        type_keys = [key for key in sorted_keys if ('.' not in key)]
        completion_key, suggestions = cls.dotted_completion(line, sorted_keys, compositor_defs)

        verbose_openers = ['style(', 'plot[', 'norm{']
        if suggestions and line.endswith('.'):
            return ['.'.join([completion_key, el]) for el in suggestions]
        elif not completion_key:
            return type_keys + list(compositor_defs.keys()) + verbose_openers

        if cls._inside_delims(line,'[', ']'):
            return [kw+'=' for kw in completions[completion_key][0]]

        if cls._inside_delims(line, '{', '}'):
            return ['+axiswise', '+framewise']

        style_completions = [kw+'=' for kw in completions[completion_key][1]]
        if cls._inside_delims(line, '(', ')'):
            return style_completions

        return type_keys + list(compositor_defs.keys()) + verbose_openers



@magics_class
class OptsMagic(Magics):
    """
    Magic for easy customising of normalization, plot and style options.
    Consult %%opts? for more information.
    """
    error_message = None # If not None, the error message that will be displayed
    opts_spec = None       # Next id to propagate, binding displayed object together.
    strict = False

    @classmethod
    def process_element(cls, obj):
        """
        To be called by the display hook which supplies the element to
        be displayed. Any customisation of the object can then occur
        before final display. If there is any error, a HTML message
        may be returned. If None is returned, display will proceed as
        normal.
        """
        if cls.error_message:
            if cls.strict:
                return cls.error_message
            else:
                sys.stderr.write(cls.error_message)
        if cls.opts_spec is not None:
            StoreOptions.set_options(obj, cls.opts_spec)
            cls.opts_spec = None
        return None


    @classmethod
    def _format_options_error(cls, err):
        """
        Return a fuzzy match message string based on the supplied OptionError
        """
        allowed_keywords = err.allowed_keywords
        target = allowed_keywords.target
        matches = allowed_keywords.fuzzy_match(err.invalid_keyword)
        if not matches:
            matches = allowed_keywords.values
            similarity = 'Possible'
        else:
            similarity = 'Similar'

        loaded_backends = Store.loaded_backends()
        target = 'for {0}'.format(target) if target else ''

        if len(loaded_backends) == 1:
            loaded=' in loaded backend {0!r}'.format(loaded_backends[0])
        else:
            backend_list = ', '.join(['%r'% b for b in loaded_backends[:-1]])
            loaded=' in loaded backends {0} and {1!r}'.format(backend_list,
                                                            loaded_backends[-1])

        suggestion = ("If you believe this keyword is correct, please make sure "
                      "the backend has been imported or loaded with the "
                      "notebook_extension.")

        group = '{0} option'.format(err.group_name) if err.group_name else 'keyword'
        msg=('Unexpected {group} {kw} {target}{loaded}.\n\n'
             '{similarity} keywords in the currently active '
             '{current_backend} backend are: {matches}\n\n{suggestion}')
        return msg.format(kw="'%s'" % err.invalid_keyword,
                          target=target,
                          group=group,
                          loaded=loaded, similarity=similarity,
                          current_backend=repr(Store.current_backend),
                          matches=matches,
                          suggestion=suggestion)

    @classmethod
    def register_custom_spec(cls, spec):
        spec, _ = StoreOptions.expand_compositor_keys(spec)
        try:
            StoreOptions.validate_spec(spec)
        except OptionError as e:
            cls.error_message = cls._format_options_error(e)

        cls.opts_spec = spec

    @classmethod
    def _partition_lines(cls, line, cell):
        """
        Check the code for additional use of %%opts. Enables
        multi-line use of %%opts in a single call to the magic.
        """
        if cell is None: return (line, cell)
        specs, code = [line], []
        for line in cell.splitlines():
            if line.strip().startswith('%%opts'):
                specs.append(line.strip()[7:])
            else:
                code.append(line)
        return ' '.join(specs), '\n'.join(code)


    @line_cell_magic
    def opts(self, line='', cell=None):
        """
        The opts line/cell magic with tab-completion.

        %%opts [ [path] [normalization] [plotting options] [style options]]+

        path:             A dotted type.group.label specification
                          (e.g. Image.Grayscale.Photo)

        normalization:    List of normalization options delimited by braces.
                          One of | -axiswise | -framewise | +axiswise | +framewise |
                          E.g. { +axiswise +framewise }

        plotting options: List of plotting option keywords delimited by
                          square brackets. E.g. [show_title=False]

        style options:    List of style option keywords delimited by
                          parentheses. E.g. (lw=10 marker='+')

        Note that commas between keywords are optional (not
        recommended) and that keywords must end in '=' without a
        separating space.

        More information may be found in the class docstring of
        util.parser.OptsSpec.
        """
        line, cell = self._partition_lines(line, cell)
        try:
            spec = OptsSpec.parse(line, ns=self.shell.user_ns)
        except SyntaxError:
            display(HTML("<b>Invalid syntax</b>: Consult <tt>%%opts?</tt> for more information."))
            return

        # Make sure the specified elements exist in the loaded backends
        available_elements = set()
        for backend in Store.loaded_backends():
            available_elements |= set(Store.options(backend).children)

        spec_elements = set(k.split('.')[0] for k in spec.keys())
        unknown_elements = spec_elements - available_elements
        if unknown_elements:
            msg = ("<b>WARNING:</b> Unknown elements {unknown} not registered "
                   "with any of the loaded backends.")
            display(HTML(msg.format(unknown=', '.join(unknown_elements))))

        if cell:
            self.register_custom_spec(spec)
            # Process_element is invoked when the cell is run.
            self.shell.run_cell(cell, store_history=STORE_HISTORY)
        else:
            try:
                StoreOptions.validate_spec(spec)
            except OptionError as e:
                OptsMagic.error_message = None
                sys.stderr.write(self._format_options_error(e))
                if self.strict:
                    display(HTML('Options specification will not be applied.'))
                    return

            with options_policy(skip_invalid=True, warn_on_skip=False):
                StoreOptions.apply_customizations(spec, Store.options())
        OptsMagic.error_message = None



@magics_class
class TimerMagic(Magics):
    """
    A line magic for measuring the execution time of multiple cells.

    After you start/reset the timer with '%timer start' you may view
    elapsed time with any subsequent calls to %timer.
    """

    start_time = None

    @staticmethod
    def elapsed_time():
        seconds = time.time() -  TimerMagic.start_time
        minutes = seconds // 60
        hours = minutes // 60
        return "Timer elapsed: %02d:%02d:%02d" % (hours, minutes % 60, seconds % 60)

    @classmethod
    def option_completer(cls, k,v):
        return ['start']

    @line_magic
    def timer(self, line=''):
        """
        Timer magic to print initial date/time information and
        subsequent elapsed time intervals.

        To start the timer, run:

        %timer start

        This will print the start date and time.

        Subsequent calls to %timer will print the elapsed time
        relative to the time when %timer start was called. Subsequent
        calls to %timer start may also be used to reset the timer.
        """
        if line.strip() not in ['', 'start']:
            print("Invalid argument to %timer. For more information consult %timer?")
            return
        elif line.strip() == 'start':
            TimerMagic.start_time = time.time()
            timestamp = time.strftime("%Y/%m/%d %H:%M:%S")
            print("Timer start: %s" % timestamp)
            return
        elif self.start_time is None:
            print("Please start timer with %timer start. For more information consult %timer?")
        else:
            print(self.elapsed_time())


def load_magics(ip):
    ip.register_magics(TimerMagic)
    ip.register_magics(OutputMagic)

    if pyparsing is None:  print("%opts magic unavailable (pyparsing cannot be imported)")
    else: ip.register_magics(OptsMagic)

    if pyparsing is None: print("%compositor magic unavailable (pyparsing cannot be imported)")
    else: ip.register_magics(CompositorMagic)


    # Configuring tab completion
    ip.set_hook('complete_command', TimerMagic.option_completer, str_key = '%timer')
    ip.set_hook('complete_command', CompositorMagic.option_completer, str_key = '%compositor')

    ip.set_hook('complete_command', OutputMagic.option_completer, str_key = '%output')
    ip.set_hook('complete_command', OutputMagic.option_completer, str_key = '%%output')

    OptsCompleter.setup_completer()
    ip.set_hook('complete_command', OptsCompleter.option_completer, str_key = '%%opts')
    ip.set_hook('complete_command', OptsCompleter.option_completer, str_key = '%opts')
