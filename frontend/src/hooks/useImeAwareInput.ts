import {
	useCallback,
	useState,
	type ChangeEvent,
	type CompositionEvent,
} from "react";

type InputElement = HTMLInputElement | HTMLTextAreaElement;

/**
 * Windows/macOS IMEs fire change events with Latin transliteration before the
 * final Devanagari glyph is committed. Skip search/submit until composition ends.
 */
export function useImeAwareInput(initialValue = "") {
	const [value, setValue] = useState(initialValue);
	const [isComposing, setIsComposing] = useState(false);

	const onCompositionStart = useCallback(() => {
		setIsComposing(true);
	}, []);

	const onCompositionEnd = useCallback(
		(event: CompositionEvent<InputElement>) => {
			setIsComposing(false);
			setValue(event.currentTarget.value);
		},
		[]
	);

	const bindInput = {
		value,
		onChange: (event: ChangeEvent<InputElement>) => {
			setValue(event.target.value);
		},
		onCompositionStart,
		onCompositionEnd,
	};

	return {
		value,
		setValue,
		isComposing,
		bindInput,
	};
}
