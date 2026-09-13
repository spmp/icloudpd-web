import { useEffect, useState } from "react";
import {
  Modal,
  ModalOverlay,
  ModalContent,
  ModalHeader,
  ModalBody,
  ModalFooter,
  Button,
  Input,
  FormControl,
  FormLabel,
  Text,
  VStack,
  Flex,
} from "@chakra-ui/react";
import { ApiError } from "@/api/client";
import { mfaApi } from "@/api/mfa";
import { pushError } from "@/store/toastStore";

interface MFAModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCancel: () => Promise<void> | void;
  policyName: string;
}

export function MFAModal({
  isOpen,
  onClose,
  onCancel,
  policyName,
}: MFAModalProps) {
  const [code, setCode] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [hasSubmitted, setHasSubmitted] = useState(false);
  const [error, setError] = useState<string | undefined>();

  // Apple 2FA codes are exactly six digits; icloudpd exits on a bad code,
  // so reject malformed input client-side instead of burning the attempt.
  const isValidCode = /^\d{6}$/.test(code);

  // Reset transient state each time the modal is re-opened for a new prompt.
  useEffect(() => {
    if (isOpen) {
      setCode("");
      setHasSubmitted(false);
      setError(undefined);
    }
  }, [isOpen]);

  const handleSubmit = async () => {
    if (!isValidCode) {
      setError("Enter the 6-digit verification code (digits only).");
      return;
    }
    setIsSubmitting(true);
    setError(undefined);
    try {
      await mfaApi.submit(policyName, code);
      setHasSubmitted(true);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
        pushError(err.message, err.errorId);
      } else {
        setError("Failed to submit MFA code");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCancel = async () => {
    setIsCancelling(true);
    try {
      await onCancel();
      setCode("");
      onClose();
    } finally {
      setIsCancelling(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleCancel}
      isCentered
      closeOnOverlayClick={false}
      closeOnEsc={false}
    >
      <ModalOverlay backdropFilter="blur(4px)" />
      <ModalContent borderRadius="xl">
        <ModalHeader>Apple 2FA verification</ModalHeader>
        <ModalBody>
          <VStack spacing={4} align="stretch">
            {hasSubmitted ? (
              <Text fontSize="sm" color="gray.600">
                Code submitted — waiting for icloudpd to verify with Apple.
                This modal will close automatically once verification
                finishes. If Apple rejects the code, the run stops — start
                it again to retry with a fresh code.
              </Text>
            ) : (
              <Text fontSize="sm" color="gray.600">
                Apple should push a 6-digit code to your trusted devices. If
                none arrives, you can generate one manually on an iPhone or
                iPad: turn on Airplane Mode, then open Settings &gt; [your
                name] &gt; Sign-In &amp; Security and a code popup appears.
                No code can also mean an outdated icloudpd-web — Apple
                changes its sign-in flow occasionally, so make sure you are
                on the latest version. Click Cancel to abort this run.
              </Text>
            )}
            <FormControl>
              <FormLabel>
                {isSubmitting ? (
                  <Flex gap={2} align="center">
                    <Text>Submitting...</Text>
                  </Flex>
                ) : hasSubmitted ? (
                  `Verifying with Apple...`
                ) : (
                  `Verification code`
                )}
              </FormLabel>
              <Input
                type="text"
                inputMode="numeric"
                maxLength={6}
                value={code}
                onChange={(e) =>
                  setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
                }
                onKeyDown={(e) => {
                  if (e.key === "Enter" && isValidCode) {
                    handleSubmit();
                  }
                }}
                placeholder="6-digit code"
                isDisabled={isSubmitting || isCancelling || hasSubmitted}
              />
            </FormControl>
            {error && (
              <Text color="red.500" fontSize="sm">
                {error}
              </Text>
            )}
          </VStack>
        </ModalBody>
        <ModalFooter>
          <Button
            variant="ghost"
            mr={3}
            onClick={handleCancel}
            isDisabled={isSubmitting}
            isLoading={isCancelling}
          >
            Cancel &amp; stop run
          </Button>
          <Button
            colorScheme="blue"
            onClick={handleSubmit}
            isDisabled={
              !isValidCode || isSubmitting || isCancelling || hasSubmitted
            }
            isLoading={isSubmitting}
          >
            Submit
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
