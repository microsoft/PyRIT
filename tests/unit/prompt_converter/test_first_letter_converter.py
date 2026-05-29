# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.prompt_converter import FirstLetterConverter


# Test that the default converter settings produce the expected result
async def test_first_letter_converter_default():
    converter = FirstLetterConverter()
    prompt = "Lorem ipsum dolor sit amet"
    output = await converter.convert_async(prompt=prompt, input_type="text")
    assert output.output_text == "L i d s a"
    assert output.output_type == "text"


# Test that the converter produces the expected result with whitespace
async def test_first_letter_converter_whitespace():
    converter = FirstLetterConverter()
    prompt = "Lorem\nipsum\tdolor\nsit\tamet"
    output = await converter.convert_async(prompt=prompt, input_type="text")
    assert output.output_text == "L i d s a"
    assert output.output_type == "text"


# Test that the converter produces the expected result with a different separator
async def test_first_letter_converter_dashes():
    converter = FirstLetterConverter(letter_separator="-")
    prompt = "Lorem ipsum dolor sit amet"
    output = await converter.convert_async(prompt=prompt, input_type="text")
    assert output.output_text == "L-i-d-s-a"
    assert output.output_type == "text"


# Test that the converter produces the expected result with French punctuation
async def test_first_letter_converter_french():
    converter = FirstLetterConverter()
    prompt = """
    En 1815, M. Charles-François-Bienvenu Myriel était évêque de Digne.
    C'était un vieillard d'environ soixante-quinze ans ; il occupait le siége de Digne depuis 1806.
    """
    output = await converter.convert_async(prompt=prompt, input_type="text")
    assert output.output_text == "E 1 M C M é é d D C u v d s a i o l s d D d 1"
    assert output.output_type == "text"


# Test that the converter produces the expected result with non-Latin alphabets
async def test_first_letter_converter_japanese():
    converter = FirstLetterConverter()
    prompt = """
    ふるいけ や
    かわず とびこむ
    みず の おと
    """
    output = await converter.convert_async(prompt=prompt, input_type="text")
    assert output.output_text == "ふ や か と み の お"
    assert output.output_type == "text"
