import re

from lfx.custom.custom_component.component import Component
from lfx.io import (
    BoolInput,
    DropdownInput,
    IntInput,
    MessageInput,
    MessageTextInput,
    Output,
)
from lfx.schema.message import Message


class ConditionalRouterComponent(Component):
    display_name = "If-Else"
    description = "Routes an input message based on text comparison or empty/not-empty checks."
    documentation: str = "https://docs.langflow.org/if-else"
    icon = "split"
    name = "ConditionalRouter"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__iteration_updated = False

    inputs = [
        MessageTextInput(
            name="input_text",
            display_name="Text Input",
            info="The primary text input for the operation.",
            required=True,
        ),
        DropdownInput(
            name="operator",
            display_name="Operator",
            options=[
                "equals",
                "not equals",
                "contains",
                "starts with",
                "ends with",
                "regex",
                "is empty",
                "is not empty",
                "less than",
                "less than or equal",
                "greater than",
                "greater than or equal",
            ],
            info="The operator to apply for comparing the text.",
            value="equals",
            real_time_refresh=True,
        ),
        MessageTextInput(
            name="match_text",
            display_name="Match Text",
            info="The text input to compare against. Not required for 'is empty' and 'is not empty'.",
            required=False,
        ),
        BoolInput(
            name="case_sensitive",
            display_name="Case Sensitive",
            info="If true, the comparison will be case sensitive.",
            value=True,
            advanced=True,
        ),
        BoolInput(
            name="block_unmatched",
            display_name="Block Unmatched Route",
            info="If True, blocks the route that doesn't match. If False, both routes execute (the false one returns an empty string without blocking).",
            value=True,
            advanced=True,
        ),
        MessageInput(
            name="true_case_message",
            display_name="Case True",
            info="The message to pass if the condition is True.",
            advanced=True,
        ),
        MessageInput(
            name="false_case_message",
            display_name="Case False",
            info="The message to pass if the condition is False.",
            advanced=True,
        ),
        IntInput(
            name="max_iterations",
            display_name="Max Iterations",
            info="The maximum number of iterations for the conditional router.",
            value=10,
            advanced=True,
        ),
        DropdownInput(
            name="default_route",
            display_name="Default Route",
            options=["true_result", "false_result"],
            info="The default route to take when max iterations are reached.",
            value="false_result",
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            display_name="True",
            name="true_result",
            method="true_response",
            group_outputs=True,
        ),
        Output(
            display_name="False",
            name="false_result",
            method="false_response",
            group_outputs=True,
        ),
    ]

    def _pre_run_setup(self):
        self.__iteration_updated = False

    def evaluate_condition(
        self,
        input_text: str,
        match_text: str,
        operator: str,
        *,
        case_sensitive: bool,
    ) -> bool:

        # Pastikan None tidak menyebabkan error
        input_text = "" if input_text is None else str(input_text)
        match_text = "" if match_text is None else str(match_text)

        # =========================================================
        # EMPTY / NOT EMPTY
        # =========================================================

        if operator == "is empty":
            return not input_text.strip()

        if operator == "is not empty":
            return bool(input_text.strip())

        # =========================================================
        # CASE SENSITIVITY
        # =========================================================

        if not case_sensitive and operator != "regex":
            input_text = input_text.lower()
            match_text = match_text.lower()

        # =========================================================
        # TEXT COMPARISON
        # =========================================================

        if operator == "equals":
            return input_text == match_text

        if operator == "not equals":
            return input_text != match_text

        if operator == "contains":
            return match_text in input_text

        if operator == "starts with":
            return input_text.startswith(match_text)

        if operator == "ends with":
            return input_text.endswith(match_text)

        # =========================================================
        # REGEX
        # =========================================================

        if operator == "regex":
            try:
                return bool(re.match(match_text, input_text))
            except re.error:
                return False

        # =========================================================
        # NUMERIC COMPARISON
        # =========================================================

        if operator in [
            "less than",
            "less than or equal",
            "greater than",
            "greater than or equal",
        ]:
            try:
                input_num = float(input_text)
                match_num = float(match_text)

                if operator == "less than":
                    return input_num < match_num

                if operator == "less than or equal":
                    return input_num <= match_num

                if operator == "greater than":
                    return input_num > match_num

                if operator == "greater than or equal":
                    return input_num >= match_num

            except (ValueError, TypeError):
                return False

        return False

    def iterate_and_stop_once(self, route_to_stop: str):
        """Handles cycle iteration counting and branch exclusion."""

        if not self.__iteration_updated:
            self.update_ctx(
                {
                    f"{self._id}_iteration": self.ctx.get(
                        f"{self._id}_iteration", 0
                    )
                    + 1
                }
            )

            self.__iteration_updated = True

        current_iteration = self.ctx.get(
            f"{self._id}_iteration",
            0,
        )

        # Check if max iterations reached and we're trying
        # to stop the default route
        if (
            current_iteration >= self.max_iterations
            and route_to_stop == self.default_route
        ):
            # Clear all conditional exclusions
            if self._id in self.graph.conditional_exclusion_sources:
                previous_exclusions = (
                    self.graph.conditional_exclusion_sources[self._id]
                )

                self.graph.conditionally_excluded_vertices -= (
                    previous_exclusions
                )

                del self.graph.conditional_exclusion_sources[self._id]

            # Switch which route to stop
            route_to_stop = (
                "true_result"
                if route_to_stop == "false_result"
                else "false_result"
            )

            if getattr(self, "block_unmatched", True):
                self.stop(route_to_stop)
            return

        # Normal case
        if getattr(self, "block_unmatched", True):
            self.stop(route_to_stop)
            self.graph.exclude_branch_conditionally(
                self._id,
                output_name=route_to_stop,
            )

    def _resolve_case_message(
        self,
        case_message: Message | str | None,
    ) -> Message:
        """Return the override case message.

        If the case message is empty, the original input text is used.
        """

        if isinstance(case_message, Message):
            if (
                case_message.text
                or case_message.files
                or case_message.content_blocks
            ):
                return case_message

            return Message(text=self.input_text)

        if isinstance(case_message, str) and case_message:
            return Message(text=case_message)

        return Message(text=self.input_text)

    def true_response(self) -> Message:
        result = self.evaluate_condition(
            self.input_text,
            self.match_text,
            self.operator,
            case_sensitive=self.case_sensitive,
        )

        # Check max iterations
        current_iteration = self.ctx.get(
            f"{self._id}_iteration",
            0,
        )

        force_output = (
            current_iteration >= self.max_iterations
            and self.default_route == "true_result"
        )

        if result or force_output:
            true_message = self._resolve_case_message(
                self.true_case_message
            )

            self.status = true_message

            if not force_output:
                self.iterate_and_stop_once("false_result")

            return true_message

        self.iterate_and_stop_once("true_result")

        return Message(text="")

    def false_response(self) -> Message:
        result = self.evaluate_condition(
            self.input_text,
            self.match_text,
            self.operator,
            case_sensitive=self.case_sensitive,
        )

        if not result:
            false_message = self._resolve_case_message(
                self.false_case_message
            )

            self.status = false_message

            self.iterate_and_stop_once("true_result")

            return false_message

        self.iterate_and_stop_once("false_result")

        return Message(text="")

    def update_build_config(
        self,
        build_config: dict,
        field_value: str,
        field_name: str | None = None,
    ) -> dict:

        if field_name == "operator":

            # Regex tidak membutuhkan case sensitivity
            if field_value == "regex":
                build_config.pop("case_sensitive", None)

            elif "case_sensitive" not in build_config:
                case_sensitive_input = next(
                    (
                        input_field
                        for input_field in self.inputs
                        if input_field.name == "case_sensitive"
                    ),
                    None,
                )

                if case_sensitive_input:
                    build_config["case_sensitive"] = (
                        case_sensitive_input.to_dict()
                    )

        return build_config